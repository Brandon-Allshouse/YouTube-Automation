"""Tests for stats loading, retention forensics, the format bandit, predictor, topic graph and weekly brief."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pytest

from src import db as dbm
from src.analytics import pull_stats, retention_forensics, weekly_brief
from src.learning import bandit, predictor
from src.learning.topic_graph import TopicGraph

if TYPE_CHECKING:
    from src.production.script import Script


@pytest.fixture
def loaded(db: sqlite3.Connection) -> sqlite3.Connection:
    pull_stats.load_fixture_into(db)
    return db


def test_fixture_load_is_idempotent(db: sqlite3.Connection) -> None:
    assert pull_stats.load_fixture_into(db) == 24
    assert pull_stats.load_fixture_into(db) == 24
    assert db.execute("SELECT COUNT(*) FROM stats").fetchone()[0] == 24


def test_forensics_recovers_planted_signal(loaded: sqlite3.Connection) -> None:
    table = {r["tag"]: r["mean_excess_loss_per_s"] for r in retention_forensics.penalty_table(loaded)}
    assert table["list"] > table["kinetic-title"] > table["map"]
    assert table["list"] > 0 > table["map"]


def test_analyze_video_on_flat_curve() -> None:
    res = retention_forensics.analyze_video(
        10.0, [1.0] * 100, [{"start": 0, "tag": "map"}, {"start": 5, "tag": "list"}]
    )
    assert res["hook_loss_3s"] == 0.0
    assert all(s["loss_per_s"] == 0.0 for s in res["segments"])


def test_bandit_posteriors_and_idempotence(loaded: sqlite3.Connection) -> None:
    a = bandit.update(loaded)
    b = bandit.update(loaded)
    assert a == b
    assert sum(s.pulls for s in a) == 24
    assert all(s.alpha >= 1 and s.beta >= 1 for s in a)


def test_bandit_recommend_deterministic_and_explores() -> None:
    states = [bandit.ArmState("good", 30, 2, 30), bandit.ArmState("bad", 2, 30, 30)]
    rec = bandit.recommend(states, 1, seed=1)
    assert rec == ["good"]
    assert bandit.recommend(states, 3, seed=1, candidates=["new"]) == bandit.recommend(
        states, 3, seed=1, candidates=["new"]
    )
    picks = [bandit.recommend(states, 1, seed=s, candidates=["new"])[0] for s in range(200)]
    assert "new" in picks  # untried arms (Beta(1,1)) still get explored


def test_success_requires_views_and_retention(db: sqlite3.Connection) -> None:
    # a high-view, low-retention video must not count as a success
    for i, (views, avp) in enumerate([(1000, 0.6), (2000, 0.6), (9000, 0.1)]):
        vid = f"v-t{i:04d}"
        db.execute(
            "INSERT INTO videos(id, channel_id, format, title, state, seed, arm_id, created_at, updated_at) "
            "VALUES (?, 'atlas', 'short', 't', 'published', 0, ?, 'x', 'x')",
            (vid, f"arm{i}"),
        )
        db.execute(
            "INSERT INTO stats(video_id, pulled_at, hours_since_publish, views, avg_view_pct) VALUES (?, 'x', 48, ?, ?)",
            (vid, views, avp),
        )
    db.commit()
    res = dict(bandit.outcomes(db))
    assert res["arm2"] is False  # clickbait-like: lots of views, viewers leave


def test_predictor_guard_and_fit(loaded: sqlite3.Connection, db: sqlite3.Connection) -> None:
    with pytest.raises(predictor.InsufficientDataError):
        predictor.fit(loaded)
    model = predictor.fit(loaded, min_videos=10)
    q = model.predict("map-oddities|short|question|s45|maps-heavy", "atlas")["ctr"]
    s = model.predict("map-oddities|short|statement|s45|maps-heavy", "atlas")["ctr"]
    assert q > s  # fixture: question hooks have higher CTR


def test_topic_graph_suggests_related_not_duplicates() -> None:
    topics = [
        "why greenland looks huge on maps",
        "why maps distort the size of greenland and antarctica",
        "how map projections distort country sizes",
        "why greenland looks huge on maps",  # duplicate: too similar, no edge
        "compound interest explained with money",
    ]
    g = TopicGraph.build(topics)
    sugg = [t for t, _ in g.suggest([0], explored=set())]
    assert topics[2] in sugg or topics[1] in sugg
    assert topics[4] not in sugg


def test_weekly_brief_contents(loaded: sqlite3.Connection) -> None:
    md = weekly_brief.build(loaded)
    assert "# Weekly brief" in md and "| atlas |" in md and "| neon |" in md
    assert "insufficient data" not in md  # 12 videos per channel ≥ 10
    assert "Retention penalty table" in md and "list" in md


def test_kill_scale_needs_minimum_sample() -> None:
    m = {"videos": 3, "median_views": 10, "median_ctr": 0.001, "median_avg_view_pct": 0.1, "median_retained_30s": 0.1}
    assert weekly_brief.kill_scale(m, None).startswith("insufficient data")
    m["videos"] = 12
    assert "KILL" in weekly_brief.kill_scale(m, None)


def test_featurize_has_bias() -> None:
    x = predictor.featurize("a|b", "atlas", ["0:a", "1:b", "ch:atlas", "ch:neon"])
    assert np.array_equal(x, np.array([1, 1, 1, 0, 1.0]))


def test_db_helpers_used(db: sqlite3.Connection) -> None:
    assert dbm.quota_used_today(db) == 0


# --- arms derived from scripts + factored planner (session 2) ------------------------------------


def _script(**over: object) -> Script:
    from src.production.script import Script, load_script
    from src.production.thumbnail import DEMO_SCRIPT

    base = load_script(DEMO_SCRIPT).model_dump()
    base.update(over)
    return Script.model_validate(base)


def test_arm_for_demo_script() -> None:
    from src.learning import arms

    s = _script()
    arm = arms.arm_for(s)
    niche, fmt, hook, length, visual = arm.split("|")
    assert (niche, fmt, hook) == ("map-oddities", "short", "statement")
    assert length in ("s45", "s55") and visual in arms.VISUAL_MIXES
    assert arms.arm_for(s, 36.0).split("|")[3] == "s35"  # the real duration overrides the estimate


def test_arm_requires_labels() -> None:
    from src.learning import arms

    with pytest.raises(ValueError, match="niche"):
        arms.arm_for(_script(niche=None))


@pytest.mark.parametrize(
    ("fmt", "dur", "want"),
    [
        ("short", 30, "s35"),
        ("short", 49, "s45"),
        ("short", 70, "s55"),
        ("long", 300, "l6"),
        ("long", 600, "l10"),
        ("long", 2000, "l14"),
    ],
)
def test_length_buckets(fmt: str, dur: float, want: str) -> None:
    from src.learning.arms import length_bucket

    assert length_bucket(fmt, dur) == want


def test_visual_mix_weights_narration() -> None:
    from src.learning.arms import visual_mix

    seg = {"id": "a", "text": "one two three four five six", "tag": "map"}
    maps = [dict(seg, id=f"m{i}", visual=[{"type": "AnimatedMap"}]) for i in range(3)]
    titles = [dict(seg, id="t", visual=[{"type": "KineticTitle", "text": "x"}])]
    assert visual_mix(_script(segments=maps + titles)) == "maps-heavy"
    even = [
        dict(seg, id=f"e{i}", visual=[{"type": t}])
        for i, t in enumerate(["AnimatedMap", "StatCounter", "DiagramBuild", "KineticTitle"])
    ]
    assert visual_mix(_script(segments=even)) == "mixed"
    bg_only = [dict(seg, id="b", visual=[{"type": "ParticleBackground"}])]
    assert visual_mix(_script(segments=bg_only)) == "mixed"


def test_spoken_words_counts_spoken_side() -> None:
    from src.learning.arms import spoken_words

    assert spoken_words("Blame a map from *{1569|fifteen sixty-nine}*.") == 7  # sixty-nine = 2 spoken words


def test_check_niche_enforces_channel_ownership() -> None:
    from src.learning.arms import check_niche

    check_niche(_script())
    with pytest.raises(ValueError, match="belongs to channel"):
        check_niche(_script(niche="tech-history"))
    with pytest.raises(ValueError, match="unknown niche"):
        check_niche(_script(niche="no-such-niche"))


def test_script_rejects_unknown_hook() -> None:
    with pytest.raises(ValueError, match="hook"):
        _script(hook="clickbait")


def test_set_arm_validates(db: sqlite3.Connection) -> None:
    vid = dbm.add_video(db, "atlas", "short", "t")
    dbm.set_arm(db, vid, "map-oddities|short|question|s45|maps-heavy")
    assert dbm.get_video(db, vid)["arm_id"] == "map-oddities|short|question|s45|maps-heavy"  # type: ignore[index]
    for bad in ("x", "a|b|c|d|e", "map-oddities|short|q'; DROP|s45|mixed"):
        with pytest.raises(ValueError, match="malformed"):
            dbm.set_arm(db, vid, bad)


def test_plan_is_distinct_deterministic_and_on_channel(db: sqlite3.Connection) -> None:
    from src.learning.arms import CHANNEL_VISUALS, HOOKS

    a = bandit.plan(db, "atlas", n=4, seed=7)
    assert a == bandit.plan(db, "atlas", n=4, seed=7)
    assert len({s.arm for s in a}) == 4
    from src.research.common import load_niches

    atlas = {n.id for n in load_niches() if n.channel == "atlas"}
    for s in a:
        assert s.niche in atlas and s.hook in HOOKS and s.visual in CHANNEL_VISUALS["atlas"] and s.explore
    with pytest.raises(ValueError, match="no seed niches"):
        bandit.plan(db, "nobody")


def test_plan_follows_research_priors_and_top_niches(db: sqlite3.Connection) -> None:
    priors = {"empires-timelapse": 0.95, "border-history": 0.9, "map-oddities": 0.05}
    picks = [
        s.niche
        for seed in range(40)
        for s in bandit.plan(db, "atlas", n=1, seed=seed, niche_priors=priors, top_niches=2)
    ]
    assert set(picks) <= {"empires-timelapse", "border-history"}  # only the top-2 researched niches


def test_plan_learns_from_outcomes(loaded: sqlite3.Connection) -> None:
    # fixture: question hooks beat statement hooks; across many seeds the planner should prefer them
    bandit.update(loaded)
    hooks = [bandit.plan(loaded, "atlas", n=1, seed=s)[0].hook for s in range(200)]
    tried = [h for h in hooks if h in ("question", "statement")]
    assert tried.count("question") > tried.count("statement")


def test_plan_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import json

    assert bandit.main(["--db", str(tmp_path / "p.sqlite"), "plan", "--channel", "neon", "--n", "2"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out) == 2 and all(o["arm"].startswith(o["niche"] + "|short|") for o in out)
