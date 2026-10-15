"""Niche scorer: rank niches on more than views.

Components (each 0-1 across the niches being compared, weights below):
* outlier density (demand that travels beyond existing audiences)            0.20
* demand (median views of the niche's top videos, log scale)                 0.15
* demand/supply gap (attention per competing video)                          0.15
* RPM estimate (midpoint of the configured prior range)                       0.15
* visual-engine fit (how well our components explain this niche)             0.15
* evergreen prior                                                             0.08
* competition (lower median competitor subscribers is better)                0.07
* unanswered-question clusters                                                0.05

Heavy-tailed evidence (views, gap, subscribers, question counts) is ranked by percentile, so one
extreme niche can't squash the others to zero. Demand is its own term: weak competition is only
worth something where people are watching (2026-10-15: "tech history" topped the old ranking with
2.6k median views, because tiny channels made its competition and gap terms look perfect).

"Can we be genuinely better than the top channels?" is a judgement the researcher agent adds in
its brief; this script supplies the evidence. Uses the latest outputs of the other modules for
the same mode, running them if missing.

``python -m src.research.niche_score [--live]``
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from src.common.log import get_logger, log_event, new_job_id
from src.research import comment_miner, outlier_scan, velocity_radar
from src.research.common import Context, add_common_args, latest_output, make_context, write_output

log = get_logger("src.research.niche_score")

WEIGHTS = {
    "outlier_density": 0.20,
    "demand": 0.15,
    "gap": 0.15,
    "rpm": 0.15,
    "visual_fit": 0.15,
    "evergreen": 0.08,
    "competition": 0.07,
    "unanswered": 0.05,
}


def _norm(values: dict[str, float], invert: bool = False) -> dict[str, float]:
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    span = hi - lo or 1.0
    return {k: ((hi - v) if invert else (v - lo)) / span for k, v in values.items()}


def _pct(values: dict[str, float], invert: bool = False) -> dict[str, float]:
    """Percentile rank in [0, 1] (ties share their average rank); robust to heavy tails."""
    if not values:
        return {}
    if len(values) == 1:
        return dict.fromkeys(values, 0.5)
    ordered = sorted(values.values())
    n = len(ordered)
    out = {}
    for k, v in values.items():
        lo = ordered.index(v)
        hi = n - 1 - ordered[::-1].index(v)
        r = (lo + hi) / 2 / (n - 1)
        out[k] = 1 - r if invert else r
    return out


def score(
    ctx: Context, outliers: dict[str, Any], velocity: dict[str, Any], comments: dict[str, Any]
) -> list[dict[str, Any]]:
    """Weighted niche ranking with a per-component breakdown."""
    o = {n["niche"]: n for n in outliers["niches"]}
    v = {n["niche"]: n for n in velocity["niches"]}
    c = {n["niche"]: n for n in comments["niches"]}
    ids = [n.id for n in ctx.niches if n.id in o and n.id in v]
    comp = {
        # long-form outliers weigh more: Shorts out-view subscriber counts as a matter of course
        "outlier_density": _norm(
            {
                i: 0.7 * o[i].get("outlier_density_long", o[i]["outlier_density"])
                + 0.3 * o[i].get("outlier_density_shorts", o[i]["outlier_density"])
                for i in ids
            }
        ),
        "demand": _pct({i: float(o[i].get("median_views", 0)) for i in ids}),
        "gap": _pct({i: float(v[i]["gap_score"]) for i in ids}),
        "rpm": _norm({n.id: sum(n.rpm_usd) / 2 for n in ctx.niches if n.id in ids}),
        "visual_fit": {n.id: n.visual_fit for n in ctx.niches if n.id in ids},
        "evergreen": {n.id: n.evergreen for n in ctx.niches if n.id in ids},
        "competition": _pct({i: float(o[i]["median_subscribers"]) for i in ids}, invert=True),
        "unanswered": _pct(
            {i: float(sum(cl["likely_unanswered"] for cl in c.get(i, {}).get("clusters", []))) for i in ids}
        ),
    }
    ranking: list[dict[str, Any]] = []
    for n in ctx.niches:
        if n.id not in ids:
            continue
        parts = {k: round(comp[k].get(n.id, 0.0), 3) for k in WEIGHTS}
        total = sum(WEIGHTS[k] * parts[k] for k in WEIGHTS)
        ranking.append(
            {
                "niche": n.id,
                "name": n.name,
                "channel": n.channel,
                "score": round(total, 3),
                "components": parts,
                "evidence": {
                    "outlier_density": o[n.id]["outlier_density"],
                    "gap_score": v[n.id]["gap_score"],
                    "median_subscribers": o[n.id]["median_subscribers"],
                    "median_views": o[n.id].get("median_views"),
                    "rpm_prior_usd": list(n.rpm_usd),
                },
            }
        )
    ranking.sort(key=lambda r: -r["score"])
    return ranking


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_common_args(p)
    a = p.parse_args(argv)
    new_job_id("niche-score")
    ctx = make_context(a)
    outliers = latest_output("outliers", ctx.live) or outlier_scan.run(ctx)
    velocity = latest_output("velocity", ctx.live) or velocity_radar.run(ctx)
    comments = latest_output("comments", ctx.live) or comment_miner.run(ctx)
    ranking = score(ctx, outliers, velocity, comments)
    path = write_output("niche_score", {"weights": WEIGHTS, "ranking": ranking}, ctx)
    log_event(log, "niche scoring done", niches=len(ranking))
    print(
        json.dumps(
            {
                "output": path.name,
                "top": [{k: r[k] for k in ("niche", "channel", "score")} for r in ranking[:10]],
            },
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
