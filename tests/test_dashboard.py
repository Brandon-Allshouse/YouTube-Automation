"""Tests for the dashboard: scoring math, read-only collection, safe rendering and the build CLI."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from scipy import stats as sstats

from src import db as dbm
from src.common.config import rules
from src.common.paths import OUTPUT_DIR, REPO_ROOT
from src.dashboard import collect as col
from src.dashboard import score as sc
from src.dashboard import svg
from src.dashboard.build import build, fixture_connection, main
from src.dashboard.render import CSS, csp_for, render

SR = rules().scoring


def vm(vid: str, *, ch: str = "atlas", fmt: str = "long", **kw: float) -> sc.VideoMetrics:
    base: dict[str, float] = {
        "views": 1000.0, "impressions": 20000.0, "ctr": 0.05, "avg_view_pct": 0.5,
        "retained_30s": 0.6, "likes": 40.0, "comments": 10.0,
    }  # fmt: skip
    base.update(kw)
    return sc.VideoMetrics(vid, ch, fmt, **base)


PEERS = [vm(f"p{i}") for i in range(5)]


# --- VPI ---------------------------------------------------------------------------------------


def test_log_ratio_clips_and_handles_zero() -> None:
    assert sc.log_ratio(2.0, 1.0) == pytest.approx(1.0)
    assert sc.log_ratio(1000.0, 1.0) == sc.CLIP
    assert sc.log_ratio(0.0, 1.0) == -sc.CLIP
    assert sc.log_ratio(1.0, 0.0) == 0.0


def test_typical_video_scores_50() -> None:
    s = sc.score_video(vm("x"), [*PEERS, vm("x")], SR)
    assert s.vpi == pytest.approx(50.0)
    assert s.baseline_source == "peers"
    assert not s.breakout


def test_twice_as_good_everywhere_scores_about_80() -> None:
    v = vm("x", views=2000, ctr=0.1, avg_view_pct=1.0, retained_30s=1.2, likes=160, comments=40, impressions=1e9)
    s = sc.score_video(v, PEERS, SR)
    assert 78 < s.vpi < 82  # logistic(1.4) ≈ 0.80 at full confidence


@given(st.floats(min_value=1, max_value=1e7), st.floats(min_value=1, max_value=1e7))
def test_vpi_is_bounded_and_monotonic_in_views(a: float, b: float) -> None:
    lo, hi = sorted((a, b))
    s_lo = sc.score_video(vm("x", views=lo), PEERS, SR).vpi
    s_hi = sc.score_video(vm("x", views=hi), PEERS, SR).vpi
    assert 0 <= s_lo <= s_hi <= 100


def test_low_exposure_is_shrunk_toward_50() -> None:
    strong = {"views": 8000.0, "avg_view_pct": 0.9}
    few = sc.score_video(vm("x", impressions=50, **strong), PEERS, SR)
    many = sc.score_video(vm("x", impressions=1e7, **strong), PEERS, SR)
    assert 50 < few.vpi < many.vpi
    assert few.confidence < many.confidence


def test_leave_one_out_and_peer_filtering() -> None:
    other_channel = [vm(f"n{i}", ch="neon", views=1e6) for i in range(5)]
    other_format = [vm(f"s{i}", fmt="short", views=1e6) for i in range(5)]
    s = sc.score_video(vm("x"), [*PEERS, *other_channel, *other_format, vm("x")], SR)
    assert s.baseline["views"] == 1000.0  # neither neon nor Shorts nor itself


def test_priors_until_enough_peers_and_views_term_dropped() -> None:
    s = sc.score_video(vm("x", views=5000), [vm("p0")], SR)
    assert s.baseline_source == "priors"
    assert "views" not in s.components  # no prior for views
    assert set(s.components) <= {"ctr", "avg_view_pct", "retained_30s", "engagement"}


def test_shorts_ignore_ctr() -> None:
    shorts = [vm(f"p{i}", fmt="short") for i in range(5)]
    s = sc.score_video(vm("x", fmt="short", ctr=0.5), shorts, SR)
    assert "ctr" not in s.components
    assert s.vpi == pytest.approx(50.0)


def test_missing_metrics_renormalize() -> None:
    v = sc.VideoMetrics("x", "atlas", "long", views=2000.0)
    s = sc.score_video(v, PEERS, SR)
    assert set(s.components) == {"views"}
    assert s.vpi > 50


def test_breakout_needs_multiple_of_median_views() -> None:
    assert sc.score_video(vm("x", views=1000 * SR.breakout_multiple), PEERS, SR).breakout
    assert not sc.score_video(vm("x", views=1000 * SR.breakout_multiple - 1), PEERS, SR).breakout


# --- channel / portfolio ---------------------------------------------------------------------


def test_theil_sen_is_robust() -> None:
    assert sc.theil_sen([0, 1, 2, 3, 4]) == pytest.approx(1.0)
    assert sc.theil_sen([0, 1, 2, 100, 4]) == pytest.approx(1.0)  # one outlier doesn't move it
    assert sc.theil_sen([5]) == 0.0


def test_reach_growth_ratio() -> None:
    assert sc.reach_growth_ratio([100, 100, 100]) is None
    assert sc.reach_growth_ratio([100, 100, 200, 200]) == pytest.approx(2.0)
    assert sc.reach_growth_ratio([0, 0, 5, 5]) is None


def test_flat_channel_scores_50_and_growth_raises_it() -> None:
    flat = [(float(d), 50.0, False, 1000.0) for d in range(8)]
    assert sc.channel_score("a", flat, SR).score == pytest.approx(50.0)
    growing = [(float(d), 50.0, False, 4000.0 if d < 4 else 1000.0) for d in range(8)]  # newer = younger
    g = sc.channel_score("a", growing, SR)
    assert g.reach_growth == pytest.approx(4.0)
    assert g.score is not None and g.score > 60
    assert sc.channel_score("a", [], SR).score is None


def test_channel_without_enough_videos_skips_growth() -> None:
    cs = sc.channel_score("a", [(1.0, 70.0, True, 900.0), (2.0, 60.0, False, 800.0)], SR)
    assert cs.reach_growth is None and cs.breakouts == 1
    assert cs.score is not None and cs.hit_rate == 0.5


def test_portfolio_score_weights_by_scored_videos() -> None:
    a = sc.ChannelScore("a", 80.0, 80.0, 1.0, 0.0, None, 3, 0)
    b = sc.ChannelScore("b", 40.0, 40.0, 0.0, 0.0, None, 1, 0)
    none = sc.ChannelScore("c", None, None, None, 0.0, None, 0, 0)
    assert sc.portfolio_score([a, b, none]) == pytest.approx(70.0)
    assert sc.portfolio_score([none]) is None


# --- levers / bandit / cadence -----------------------------------------------------------------


def test_parse_arm() -> None:
    assert sc.parse_arm("maps|short|question|s45|maps-heavy") == {
        "niche": "maps", "format": "short", "hook": "question", "length": "s45", "visual": "maps-heavy",
    }  # fmt: skip
    assert sc.parse_arm("maps|short") == {"niche": "maps", "format": "short"}


def test_levers_shrink_small_samples() -> None:
    rows = [("a|short|question", 90.0)] + [("b|short|statement", 50.0)] * 9
    lv = {(x.dimension, x.value): x for x in sc.levers(rows)}
    q = lv[("hook", "question")]
    assert q.n == 1 and q.low_confidence
    assert q.mean_vpi == 90.0 and 54.0 < q.shrunk_vpi < 70.0  # pulled toward the overall mean
    assert sc.levers([]) == []


def test_arm_posteriors_match_scipy_and_are_deterministic() -> None:
    arms = [("good", 30.0, 10.0, 38), ("bad", 10.0, 30.0, 38), ("new", 1.0, 1.0, 0)]
    a = sc.arm_posteriors(arms, seed=1)
    assert a == sc.arm_posteriors(arms, seed=1)
    assert a[0].arm_id == "good"
    assert sum(p.p_best for p in a) == pytest.approx(1.0, abs=1e-3)
    good = next(p for p in a if p.arm_id == "good")
    assert good.low == pytest.approx(sstats.beta.ppf(0.05, 30, 10), abs=1e-4)
    assert good.low < good.mean < good.high
    assert sc.arm_posteriors([]) == []


def test_cadence_adherence_and_overposting() -> None:
    c = sc.cadence({"short": 7, "long": 1}, 1, 2)
    assert c.adherence == pytest.approx(8 / 9, abs=1e-3) and not c.notes
    over = sc.cadence({"short": 9}, 1, 2)
    assert over.notes and over.adherence is not None and over.adherence <= 1
    assert sc.cadence({}, 0, 0).adherence is None


# --- collection (read-only) ----------------------------------------------------------------------


def _file_db(tmp_path: Path) -> Path:
    path = tmp_path / "dash.sqlite"
    conn = dbm.connect(path)
    from src.analytics.pull_stats import load_fixture_into

    load_fixture_into(conn)
    conn.close()
    return path


def test_open_readonly_cannot_write(tmp_path: Path) -> None:
    conn = col.open_readonly(_file_db(tmp_path))
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM videos")
    conn.close()


def test_open_readonly_confined_and_must_exist(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="under data/"):
        col.open_readonly(REPO_ROOT / "src" / "x.sqlite")
    with pytest.raises(FileNotFoundError):
        col.open_readonly(tmp_path / "missing.sqlite")


def test_snapshot_takes_pull_closest_to_48h() -> None:
    conn = dbm.connect(":memory:")  # type: ignore[arg-type]
    vid = dbm.add_video(conn, "atlas", "long", "t")
    for h, views in ((24, 100), (47, 470), (60, 600), (200, 2000)):
        conn.execute(
            "INSERT INTO stats(video_id, pulled_at, hours_since_publish, views) VALUES (?, '2026-01-01', ?, ?)",
            (vid, h, views),
        )
    m = col.snapshot_metrics(conn, 48)
    assert len(m) == 1 and m[0].views == 470
    assert col.latest_views(conn) == {vid: 2000}


def test_collect_fixture_snapshot_shape() -> None:
    conn = fixture_connection()
    snap = col.collect(conn, source="SYNTHETIC test")
    conn.close()
    p = snap["portfolio"]
    assert p["videos_total"] == p["published"] == p["scored"] == 24
    assert p["score"] is not None and 0 <= p["score"] <= 100
    assert {c["id"] for c in snap["channels"]} == {"atlas", "neon"}
    assert all(c["platform"] == "youtube" for c in snap["channels"])
    assert snap["learning"]["arms"] and snap["learning"]["levers"]
    assert [f["state"] for f in snap["pipeline"]["funnel"]][-1] == "published"
    json.dumps(snap)  # serializable for agents


def test_research_files_are_parsed_defensively(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(col, "RESEARCH_DIR", tmp_path)
    (tmp_path / "niche_score-2026-10-01.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "niche_score-2026-09-01-fixture.json").write_text(
        json.dumps({"ranking": [{"name": "fixture", "score": 0.1}]}), encoding="utf-8"
    )
    assert col.research_summary()["niches"] == []  # newest live file is broken → nothing, not a crash
    evil = {"mode": "live", "ranking": [{"name": "<img src=x onerror=alert(1)>", "channel": "neon", "score": 0.9},
                                        {"name": "no score"}, "junk", {"name": "nan", "score": "NaN"}]}  # fmt: skip
    (tmp_path / "niche_score-2026-10-02.json").write_text(json.dumps(evil), encoding="utf-8")
    (tmp_path / "velocity-2026-10-02.json").write_text(
        json.dumps({"niches": [{"accelerating_videos": 3}, 7]}), encoding="utf-8"
    )
    (tmp_path / "unrelated.json").write_text("{}", encoding="utf-8")
    rs = col.research_summary()
    assert [n["niche"] for n in rs["niches"]] == ["<img src=x onerror=alert(1)>"]  # kept as data; escaped later
    assert rs["accelerating_videos"] == 3


def test_research_size_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(col, "RESEARCH_DIR", tmp_path)
    monkeypatch.setattr(col, "MAX_RESEARCH_BYTES", 10)
    (tmp_path / "niche_score-2026-10-02.json").write_text(json.dumps({"ranking": []}) + " " * 20, encoding="utf-8")
    assert col.research_summary()["niche_file"] == ""


# --- rendering + security ----------------------------------------------------------------------------


class _TagBalance(HTMLParser):
    VOID = frozenset({"meta", "br", "hr", "img", "input", "link"})

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.tags: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.add(tag)
        for name, _ in attrs:
            if name.startswith("on") or name == "style":
                self.errors.append(f"{tag}[{name}]")
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.add(tag)
        for name, _ in attrs:
            if name.startswith("on") or name == "style":
                self.errors.append(f"{tag}[{name}]")

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"unbalanced </{tag}>")
        else:
            self.stack.pop()


def _hostile_snapshot() -> dict[str, Any]:
    conn = fixture_connection()
    conn.execute("UPDATE videos SET title = ?, youtube_id = ? WHERE id = 'v-fx0000'",
                 ('<script>alert("x")</script> & "quotes"', "dQw4w9WgXcQ"))  # fmt: skip
    conn.execute("UPDATE videos SET youtube_id = ? WHERE id = 'v-fx0001'", ('x" onmouseover="alert(1)',))
    snap = col.collect(conn, source="SYNTHETIC test")
    conn.close()
    snap["research"]["niches"] = [{"niche": "<img src=x onerror=alert(1)>", "channel": "neon", "score": 0.5}]
    return snap


def test_render_escapes_everything_and_has_no_scripts() -> None:
    page = render(_hostile_snapshot())
    assert "<script" not in page.lower()
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in page
    assert "&lt;img src=x onerror=alert(1)&gt;" in page
    assert 'onmouseover="alert' not in page
    assert 'href="https://www.youtube.com/watch?v=dQw4w9WgXcQ"' in page  # valid id is linked
    hrefs = re.findall(r'href="([^"]+)"', page)
    assert all(h.startswith(("#", "https://www.youtube.com/watch?v=")) for h in hrefs)
    assert not re.search(r'\ssrc="', page)  # no external resources
    checker = _TagBalance()
    checker.feed(page)
    assert checker.errors == [] and checker.stack == []
    assert "script" not in checker.tags


def test_csp_pins_the_exact_stylesheet() -> None:
    page = render(_hostile_snapshot())
    digest = base64.b64encode(hashlib.sha256(CSS.encode()).digest()).decode()
    assert f"style-src &#x27;sha256-{digest}&#x27;" in page
    assert f"<style>{CSS}</style>" in page
    assert "script-src 'none'" in csp_for(CSS) and "default-src 'none'" in csp_for(CSS)
    assert page.count("<style>") == 1


def test_render_empty_database() -> None:
    conn = dbm.connect(":memory:")  # type: ignore[arg-type]
    snap = col.collect(conn)
    conn.close()
    page = render(snap)
    assert "No videos yet." in page and "SYNTHETIC" not in page
    assert snap["portfolio"]["score"] is None


def test_synthetic_banner_shown_for_fixtures() -> None:
    assert "SYNTHETIC DEMO DATA" in render(_hostile_snapshot())


# --- svg primitives -----------------------------------------------------------------------------------


def test_formatters() -> None:
    assert svg.fmt_num(1_234_567) == "1.23M" and svg.fmt_num(None) == "–" and svg.fmt_num(950) == "950"
    assert svg.fmt_pct(0.1234) == "12.3%" and svg.fmt_pct(float("nan")) == "–"
    assert svg.esc('<a href="x">') == "&lt;a href=&quot;x&quot;&gt;"


def test_charts_handle_empty_and_never_use_style_attributes() -> None:
    charts = [
        svg.gauge(None, "x"), svg.gauge(150, "x"), svg.sparkline([None, 3]), svg.sparkline([10, 90, 40]),
        svg.hbars([]), svg.hbars([("a<b", 3.0, "s-good"), ("c", -1.0, "")], center=0.0),
        svg.vpi_timeline({}), svg.vpi_timeline({"atlas": [40.0]}), svg.vpi_timeline({"a": [10, 90], "b": [50, 60, 70]}),
        svg.intervals([]), svg.intervals([("arm<1>", 0.1, 0.3, 0.6)]), svg.meter(5, 0), svg.meter(9000, 8000),
    ]  # fmt: skip
    joined = "".join(charts)
    assert "style=" not in joined and "<script" not in joined
    assert "a&lt;b" in joined and "arm&lt;1&gt;" in joined


# --- build / CLI --------------------------------------------------------------------------------------


def test_build_fixtures_writes_html_and_json() -> None:
    out = OUTPUT_DIR / "test-dashboard"
    snap = build(fixtures=True, out_dir=out)
    assert (out / "demo.html").is_file() and (out / "demo.json").is_file()
    assert json.loads((out / "demo.json").read_text(encoding="utf-8"))["portfolio"] == snap["portfolio"]
    assert not list(out.glob("*.tmp"))


def test_build_refuses_paths_outside_output(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="escapes allowed root"):
        build(fixtures=True, out_dir=REPO_ROOT / "src")
    assert main(["--db", str(REPO_ROOT / "src" / "x.sqlite"), "--out", str(OUTPUT_DIR / "test-dashboard")]) == 2


def test_build_real_mode_is_read_only(tmp_path: Path) -> None:
    db_path = _file_db(tmp_path)
    before = db_path.read_bytes()
    assert main(["--db", str(db_path), "--out", str(OUTPUT_DIR / "test-dashboard")]) == 0
    assert db_path.read_bytes() == before
