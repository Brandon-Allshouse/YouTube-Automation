"""CLI entry-point tests for compliance and learning modules (coverage of the agent-facing interfaces)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import db as dbm
from src.analytics import pull_stats
from src.compliance import distinctness
from src.learning import bandit, predictor, topic_graph
from src.production.produce import DEMO_SCRIPT


@pytest.fixture
def stats_db(tmp_path: Path) -> Path:
    p = tmp_path / "s.sqlite"
    conn = dbm.connect(p)
    pull_stats.load_fixture_into(conn)
    conn.close()
    return p


def test_distinctness_cli_register_then_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "d.sqlite"
    assert distinctness.main(["--db", str(db), "check", "--script", str(DEMO_SCRIPT)]) == 0
    assert (
        distinctness.main(["--db", str(db), "register", "--script", str(DEMO_SCRIPT), "--video-id", "v-demo00001"]) == 0
    )
    assert distinctness.main(["--db", str(db), "check", "--script", str(DEMO_SCRIPT)]) == 1  # now a duplicate
    out = capsys.readouterr().out
    assert '"passed": false' in out


def test_distinctness_skips_corrupt_fingerprints(tmp_path: Path) -> None:
    conn = dbm.connect(tmp_path / "c.sqlite")
    conn.execute(
        "INSERT INTO fingerprints(video_id, channel_id, text, created_at) VALUES ('v-bad', 'atlas', '{not json', 'x')"
    )
    conn.commit()
    assert distinctness.history(conn) == []


def test_bandit_cli(stats_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert bandit.main(["--db", str(stats_db), "update"]) == 0
    assert bandit.main(["--db", str(stats_db), "show"]) == 0
    assert bandit.main(["--db", str(stats_db), "recommend", "--n", "2", "--seed", "3"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    rec = json.loads(lines[-1])["recommend"]
    assert len(rec) == 2


def test_predictor_cli_reports_insufficient_data(stats_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert predictor.main(["--db", str(stats_db), "fit"]) == 0
    assert "not enough data" in capsys.readouterr().out


def test_predictor_cli_fit_and_predict(
    stats_db: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(predictor, "MIN_VIDEOS", 10)
    monkeypatch.setattr(predictor.fit, "__defaults__", None)
    original = predictor.fit

    def fit_low(conn, *, min_videos: int = 10, lam: float = 1.0):  # type: ignore[no-untyped-def]
        return original(conn, min_videos=min_videos, lam=lam)

    monkeypatch.setattr(predictor, "fit", fit_low)
    assert predictor.main(["--db", str(stats_db), "fit"]) == 0
    assert (
        predictor.main(
            [
                "--db",
                str(stats_db),
                "predict",
                "--arm",
                "map-oddities|short|question|s45|maps-heavy",
                "--channel",
                "atlas",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out.strip().splitlines()
    assert "fitted_on" in out[0] and "ctr" in out[-1]


def test_topic_graph_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    topics = tmp_path / "t.json"
    topics.write_text(
        json.dumps(
            [
                "why greenland looks huge on maps",
                "why maps distort the size of greenland and antarctica",
                "how map projections distort country sizes",
                "compound interest explained",
            ]
        )
    )
    assert topic_graph.main(["--topics", str(topics), "--winners", "0"]) == 0
    assert "antarctica" in capsys.readouterr().out
