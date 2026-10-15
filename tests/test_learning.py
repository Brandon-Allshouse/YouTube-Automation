"""Tests for stats loading, retention forensics, the format bandit, predictor, topic graph and weekly brief."""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from src import db as dbm
from src.analytics import pull_stats, retention_forensics, weekly_brief
from src.learning import bandit, predictor
from src.learning.topic_graph import TopicGraph


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
