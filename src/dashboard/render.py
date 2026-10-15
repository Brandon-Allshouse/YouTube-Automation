"""Render the dashboard snapshot (from :mod:`src.dashboard.collect`) as one self-contained HTML page.

Security (OWASP A03/A05, ASVS V5/V14): no JavaScript at all; a Content-Security-Policy that allows
nothing but the page's own ``<style>`` block (by SHA-256 hash); every value HTML-escaped; external
links only to validated YouTube ids, with ``noopener noreferrer``; no secrets are ever collected,
so none can be rendered. The page works offline and needs no server.
"""

from __future__ import annotations

import base64
import hashlib
import re
from datetime import datetime
from typing import Any

from src.dashboard.svg import (
    esc,
    fmt_num,
    fmt_pct,
    gauge,
    hbars,
    intervals,
    meter,
    score_class,
    sparkline,
    vpi_timeline,
)

_YT_ID = re.compile(r"[A-Za-z0-9_-]{11}")

CSS = """
:root{--bg:#0d1117;--panel:#151b23;--panel2:#1b232d;--line:#2a3441;--text:#e6edf3;--muted:#8b98a7;
--good:#3fb950;--mid:#d29922;--bad:#f85149;--accent:#58a6ff;--s0:#58a6ff;--s1:#f0883e;--s2:#a371f7;--s3:#3fb950;
--s4:#db61a2;--s5:#e3b341;color-scheme:dark}
@media (prefers-color-scheme: light){:root{--bg:#f6f8fa;--panel:#ffffff;--panel2:#f0f3f6;--line:#d0d7de;
--text:#1f2328;--muted:#59636e;--good:#1a7f37;--mid:#9a6700;--bad:#cf222e;--accent:#0969da;color-scheme:light}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--accent)}header{padding:20px 16px 8px;max-width:1240px;margin:0 auto}
h1{font-size:22px;margin:0 0 2px}h2{font-size:17px;margin:0 0 12px}h3{font-size:14px;margin:0 0 8px;color:var(--muted)}
.sub{color:var(--muted);font-size:13px}
.banner{background:var(--mid);color:#000;padding:8px 12px;border-radius:8px;margin:10px 0;font-weight:600}
nav{position:sticky;top:0;z-index:2;background:var(--bg);border-bottom:1px solid var(--line)}
nav div{max-width:1240px;margin:0 auto;padding:8px 16px;display:flex;gap:14px;overflow-x:auto;white-space:nowrap}
nav a{color:var(--muted);text-decoration:none;font-weight:600}nav a:hover{color:var(--text)}
main{max-width:1240px;margin:0 auto;padding:8px 16px 40px}
section{margin:22px 0}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;min-width:0}
.hero{display:grid;grid-template-columns:minmax(220px,300px) 1fr;gap:16px;align-items:stretch}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
.tile{background:var(--panel2);border-radius:10px;padding:10px 12px}
.tile b{display:block;font-size:22px;font-variant-numeric:tabular-nums}.tile span{color:var(--muted);font-size:12px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
.card h3{color:var(--text);font-size:15px}.kv{display:grid;grid-template-columns:auto 1fr;gap:2px 12px;font-size:13px}
.kv dt{color:var(--muted)}.kv dd{margin:0;text-align:right;font-variant-numeric:tabular-nums}
.pill{display:inline-block;border-radius:999px;padding:1px 8px;font-size:12px;font-weight:600;background:var(--panel2)}
.pill.s-good{background:var(--good);color:#fff}.pill.s-mid{background:var(--mid);color:#000}
.pill.s-bad{background:var(--bad);color:#fff}.pill.s-none{color:var(--muted)}
.table-wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{color:var(--muted);font-weight:600;position:sticky;top:0;background:var(--panel)}
td.l,th.l{text-align:left}td.title{max-width:340px;overflow:hidden;text-overflow:ellipsis}
.empty{color:var(--muted);margin:6px 0}.note{color:var(--muted);font-size:12px}
svg{width:100%;height:auto;display:block}svg.gauge{max-width:300px;margin:0 auto}svg.spark{width:120px;height:32px}
svg.meter{max-width:260px;height:14px}
svg text{fill:var(--text)}svg .axis{fill:var(--muted);font-size:11px}svg .val{font-size:11px}
.g-track{fill:none;stroke:var(--line);stroke-width:18;stroke-linecap:round}
.g-arc{fill:none;stroke-width:18;stroke-linecap:round}.g-arc.s-good{stroke:var(--good)}.g-arc.s-mid{stroke:var(--mid)}
.g-arc.s-bad{stroke:var(--bad)}.g-value{font-size:44px;font-weight:700}.g-label{fill:var(--muted);font-size:13px}
.g-track-fill{fill:var(--panel2)}
.grid{stroke:var(--line);stroke-width:1}.baseline{stroke:var(--muted);stroke-width:1;stroke-dasharray:4 4}
.hitline{stroke:var(--good);stroke-width:1;stroke-dasharray:2 4}
.line{fill:none;stroke-width:2}.dot{stroke:var(--panel);stroke-width:1.5}
.spark-line{fill:none;stroke:var(--accent);stroke-width:2}
.bar{fill:var(--accent)}.bar.s-good{fill:var(--good)}.bar.s-mid{fill:var(--mid)}.bar.s-bad{fill:var(--bad)}
.bar.muted{fill:var(--line)}
.ci{stroke:var(--accent);stroke-width:4;stroke-linecap:round;opacity:.45}.ci-dot{fill:var(--accent)}
.series-0{stroke:var(--s0);fill:var(--s0)}.series-1{stroke:var(--s1);fill:var(--s1)}.series-2{stroke:var(--s2);fill:var(--s2)}
.series-3{stroke:var(--s3);fill:var(--s3)}.series-4{stroke:var(--s4);fill:var(--s4)}.series-5{stroke:var(--s5);fill:var(--s5)}
polyline.line{fill:none}
.legend span{margin-right:12px;font-size:12px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px}
.legend .series-0{background:var(--s0)}.legend .series-1{background:var(--s1)}.legend .series-2{background:var(--s2)}
.legend .series-3{background:var(--s3)}.legend .series-4{background:var(--s4)}.legend .series-5{background:var(--s5)}
@media (max-width:820px){.hero,.grid2{grid-template-columns:1fr}}
"""


def csp_for(css: str) -> str:
    """CSP allowing only this exact stylesheet (hash-pinned) and nothing else."""
    digest = base64.b64encode(hashlib.sha256(css.encode("utf-8")).digest()).decode()
    return (
        f"default-src 'none'; style-src 'sha256-{digest}'; img-src 'none'; script-src 'none'; "
        "base-uri 'none'; form-action 'none'"
    )


def _when(iso: str | None) -> str:
    if not iso:
        return "–"
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return esc(iso)


def _growth(g: float | None) -> str:
    return "– (needs 4 scored videos)" if g is None else f"{g:.2f}× views per upload"


def _int(v: float | None) -> str:
    return "–" if v is None else f"{v:.0f}"


def _pill(v: float | None) -> str:
    return f'<span class="pill {score_class(v)}">{"–" if v is None else f"{v:.0f}"}</span>'


def _tile(value: str, label: str) -> str:
    return f'<div class="tile"><b>{value}</b><span>{esc(label)}</span></div>'


def _yt_link(vid: str | None, text: str) -> str:
    if vid and _YT_ID.fullmatch(vid):
        return f'<a href="https://www.youtube.com/watch?v={vid}" rel="noopener noreferrer">{esc(text)}</a>'
    return esc(text)


def render(snap: dict[str, Any]) -> str:
    """The complete HTML document."""
    p = snap["portfolio"]
    scoring = snap["scoring"]
    parts: list[str] = []
    synthetic = "synthetic" in str(snap["source"]).lower()

    # --- header + hero -------------------------------------------------------------------
    parts.append(
        f'<header><h1>Portfolio dashboard</h1><div class="sub">Generated {esc(_when(snap["generated_at"]))} '
        f"from {esc(snap['source'])}. Rebuild: <code>bash scripts/task.sh dashboard</code></div>"
    )
    if synthetic:
        parts.append('<div class="banner">SYNTHETIC DEMO DATA: fixture videos, not real channel performance.</div>')
    parts.append("</header>")
    parts.append(
        '<nav><div><a href="#overview">Overview</a><a href="#accounts">Accounts</a><a href="#videos">Videos</a>'
        '<a href="#levers">What works</a><a href="#learning">Learning</a><a href="#pipeline">Pipeline</a>'
        '<a href="#research">Research</a><a href="#method">How scores work</a></div></nav><main>'
    )
    tiles = "".join(
        [
            _tile(fmt_num(p["views_total"]), "total views"),
            _tile(str(p["published"]), "videos published"),
            _tile("–" if p["median_vpi"] is None else f"{p['median_vpi']:.0f}", "median VPI (50 = typical)"),
            _tile(str(p["hits"]), f"hits (VPI ≥ {scoring['hit_threshold']:.0f})"),
            _tile(str(p["breakouts"]), f"breakouts (≥ {scoring['breakout_multiple']:.0f}× median views)"),
            _tile(str(p["review_backlog"]), "scripts awaiting your review"),
            _tile(str(p["scheduled"]), "scheduled uploads"),
            _tile(str(p["failed_jobs"]), "failed jobs"),
        ]
    )
    quota = snap["quota"]
    parts.append(
        f'<section id="overview" class="hero"><div class="panel">{gauge(p["score"], "Portfolio score")}'
        f'<p class="note">Channel scores weighted by scored videos. 50 = performing like your own typical video; '
        f"higher means the algorithm is pushing you harder.</p></div>"
        f'<div class="panel"><div class="tiles">{tiles}</div>'
        f"<h3>API quota today</h3>{meter(quota['used_today'], quota['cap'])}"
        f'<div class="note">{esc(fmt_num(quota["used_today"]))} / {esc(fmt_num(quota["cap"]))} units</div>'
        "</div></section>"
    )

    # --- accounts --------------------------------------------------------------------------
    cards = []
    for ch in snap["channels"]:
        s = ch["score"]
        cad = ch["cadence"]
        vpis = [v["vpi"] for v in reversed(snap["videos"]) if v["channel"] == ch["id"] and v["vpi"] is not None]
        adherence = "–" if cad["adherence"] is None else fmt_pct(cad["adherence"], 0)
        notes = "".join(f'<div class="note">⚠ {esc(n)}</div>' for n in cad["notes"])
        cards.append(
            f'<div class="panel card"><h3>{esc(ch["name"])} {_pill(s["score"])}</h3>'
            f'<div class="sub">{esc(ch["platform"])} · {esc(ch["niche"])} · theme {esc(ch["theme"])} · '
            f"voice {esc(ch['voice'])} · {esc(ch['music'])} music</div>{sparkline(vpis[-12:])}"
            '<dl class="kv">'
            f"<dt>Form (recent VPI)</dt><dd>{_int(s['form'])}</dd>"
            f"<dt>Reach growth</dt><dd>{_growth(s['reach_growth'])}</dd>"
            f"<dt>Hit rate</dt><dd>{fmt_pct(s['hit_rate'], 0)}</dd>"
            f"<dt>Trend</dt><dd>{s['trend_per_video']:+.1f} VPI / video</dd>"
            f"<dt>Breakouts</dt><dd>{s['breakouts']}</dd>"
            f"<dt>Published / total</dt><dd>{ch['published']} / {ch['videos_total']}</dd>"
            f"<dt>Lifetime views</dt><dd>{fmt_num(ch['views_total'])}</dd>"
            f"<dt>Cadence (7 d)</dt><dd>{cad['shorts']}/{cad['shorts_target']} Shorts · "
            f"{cad['long']}/{cad['long_target']} long · {adherence}</dd>"
            f"<dt>Last upload</dt><dd>{esc(_when(ch['last_published']))}</dd>"
            f"<dt>Next slot</dt><dd>{esc(_when(ch['next_scheduled']))}</dd>"
            f"<dt>Kill / scale</dt><dd>{esc(ch['verdict'])}</dd>"
            f"</dl>{notes}</div>"
        )
    exports = snap["exports"]
    export_note = ", ".join(f"{esc(k)}: {v}" for k, v in exports.items()) if exports else "none yet"
    parts.append(
        '<section id="accounts"><h2>Accounts</h2><div class="cards">'
        f"{''.join(cards) or '<p class=empty>No channels configured.</p>'}</div>"
        f'<p class="note">Cross-post packages ready for manual upload: {export_note}.</p></section>'
    )

    # --- performance over time ----------------------------------------------------------
    series: dict[str, list[float]] = {}
    for v in sorted(snap["videos"], key=lambda r: str(r["published_at"] or "")):
        if v["vpi"] is not None:
            series.setdefault(v["channel"], []).append(v["vpi"])
    legend = "".join(f'<span><i class="series-{k % 6}"></i>{esc(name)}</span>' for k, name in enumerate(sorted(series)))
    parts.append(
        f'<section class="panel"><h2>Performance per video</h2><div class="legend">{legend}</div>'
        f"{vpi_timeline(series, hit=scoring['hit_threshold'])}"
        f'<p class="note">Dashed line = 50 (channel-typical). Dotted green = hit threshold.</p></section>'
    )

    # --- videos leaderboard -------------------------------------------------------------
    rows = []
    ranked = sorted(snap["videos"], key=lambda r: (r["vpi"] is None, -(r["vpi"] or 0)))
    for v in ranked[:200]:
        badge = " 🚀" if v["breakout"] else ""
        rows.append(
            "<tr>"
            f"<td>{_pill(v['vpi'])}{badge}</td>"
            f'<td class="l title">{_yt_link(v["youtube_id"], v["title"])}</td>'
            f'<td class="l">{esc(v["channel"])}</td><td class="l">{esc(v["format"])}</td>'
            f'<td class="l">{esc(v["state"])}</td>'
            f"<td>{fmt_num(v['views_48h'])}</td><td>{fmt_num(v['views_lifetime'])}</td>"
            f"<td>{fmt_pct(v['ctr'])}</td><td>{fmt_pct(v['avg_view_pct'], 0)}</td>"
            f"<td>{fmt_pct(v['retained_30s'], 0)}</td><td>{fmt_pct(v['engagement'])}</td>"
            f'<td class="l">{esc(v["baseline"] or "–")}</td>'
            f'<td class="l">{esc(_when(v["published_at"]))}</td></tr>'
        )
    parts.append(
        '<section id="videos" class="panel"><h2>Videos (ranked by VPI)</h2><div class="table-wrap"><table><thead><tr>'
        '<th>VPI</th><th class="l">Title</th><th class="l">Channel</th><th class="l">Format</th>'
        '<th class="l">State</th>'
        f"<th>Views {scoring['snapshot_hours']} h</th><th>Views now</th><th>CTR</th><th>Avg view</th>"
        '<th>Kept 30 s</th><th>Engagement</th><th class="l">Baseline</th><th class="l">Published</th>'
        "</tr></thead><tbody>"
        + ("".join(rows) or '<tr><td class="l" colspan="13">No videos yet.</td></tr>')
        + "</tbody></table></div></section>"
    )

    # --- levers ----------------------------------------------------------------------------
    lev = snap["learning"]["levers"]
    by_dim: dict[str, list[dict[str, Any]]] = {}
    for lv in lev:
        by_dim.setdefault(lv["dimension"], []).append(lv)
    blocks = []
    for dim, items in by_dim.items():
        bars = [
            (
                f"{lv['value']} (n={lv['n']}{', low conf.' if lv['low_confidence'] else ''})",
                float(lv["shrunk_vpi"]),
                "muted" if lv["low_confidence"] else score_class(lv["shrunk_vpi"]),
            )
            for lv in items
        ]
        overall = items[0]["shrunk_vpi"] - items[0]["lift"]
        blocks.append(f'<div class="panel"><h3>{esc(dim)}</h3>{hbars(bars, center=overall)}</div>')
    pen = snap["learning"]["retention_penalties"]
    pen_bars = [
        (
            f"{r['tag']} ({r['segments']} seg.)",
            float(r["excess_loss_per_s"]) * 100,
            "s-bad" if r["excess_loss_per_s"] > 0 else "s-good",
        )
        for r in pen
    ]
    pen_chart = hbars(pen_bars, center=0.0, value_fmt="{:+.2f}")
    parts.append(
        '<section id="levers"><h2>What works (levers you can pull)</h2>'
        '<p class="note">Mean VPI per choice, shrunk toward the overall mean so one lucky video can\'t top the list. '
        "Bars diverge from your overall average.</p>"
        f'<div class="grid2">{"".join(blocks) or "<p class=empty>No arms with scored videos yet.</p>"}'
        f'<div class="panel"><h3>Retention: visual types that lose viewers</h3>'
        f'{pen_chart}<p class="note">Extra % of viewers lost per second '
        "during each visual type vs the same video's median (positive = worse).</p></div></div></section>"
    )

    # --- learning (bandit) ----------------------------------------------------------------
    arms = snap["learning"]["arms"]
    arm_rows = "".join(
        f'<tr><td class="l">{esc(a["arm_id"])}</td><td>{a["mean"]:.2f}</td><td>{a["low"]:.2f}–{a["high"]:.2f}</td>'
        f"<td>{fmt_pct(a['p_best'], 0)}</td><td>{a['pulls']}</td></tr>"
        for a in arms
    )
    parts.append(
        '<section id="learning" class="grid2"><div class="panel"><h2>Bandit: which formula to test next</h2>'
        f"{intervals([(a['arm_id'], a['low'], a['mean'], a['high']) for a in arms[:10]])}"
        '<p class="note">Dot = posterior success rate (beat the channel median on views and kept attention); '
        "bar = 90 % credible interval. Wide bars need more tests.</p></div>"
        '<div class="panel"><h3>Arms by probability of being best</h3><div class="table-wrap"><table><thead><tr>'
        '<th class="l">Arm</th><th>Mean</th><th>90 % CI</th><th>P(best)</th><th>Videos</th></tr></thead><tbody>'
        + (arm_rows or '<tr><td class="l" colspan="5">No arms yet.</td></tr>')
        + "</tbody></table></div></div></section>"
    )

    # --- pipeline ----------------------------------------------------------------------------
    pipe = snap["pipeline"]
    funnel = hbars([(f["state"], float(f["count"]), "") for f in pipe["funnel"]])
    sched_rows = "".join(
        f'<tr><td class="l">{esc(_when(s["slot"]))}</td><td class="l">{esc(s["channel"])}</td>'
        f'<td class="l">{esc(s["format"])}</td><td class="l title">{esc(s["title"])}</td></tr>'
        for s in snap["schedule"]
    )
    ev_rows = "".join(
        f'<tr><td class="l">{esc(_when(e["at"]))}</td><td class="l">{esc(e["video_id"])}</td>'
        f'<td class="l">{esc(e["from"] or "–")} → {esc(e["to"])}</td><td class="l">{esc(e["actor"])}</td>'
        f'<td class="l title">{esc(e["note"])}</td></tr>'
        for e in pipe["events"]
    )
    fail_rows = "".join(
        f'<tr><td class="l">{esc(_when(j["at"]))}</td><td class="l">{esc(j["kind"])}</td>'
        f'<td class="l">{esc(j["video_id"] or "–")}</td><td class="l title">{esc(j["detail"])}</td></tr>'
        for j in pipe["failed_jobs"]
    )
    parts.append(
        '<section id="pipeline"><h2>Pipeline</h2><div class="grid2">'
        f'<div class="panel"><h3>Videos by state</h3>{funnel}</div>'
        '<div class="panel"><h3>Upcoming uploads</h3><div class="table-wrap"><table><thead><tr><th class="l">Slot</th>'
        '<th class="l">Channel</th><th class="l">Format</th><th class="l">Title</th></tr></thead><tbody>'
        + (sched_rows or '<tr><td class="l" colspan="4">Nothing scheduled.</td></tr>')
        + "</tbody></table></div></div>"
        '<div class="panel"><h3>Recent activity</h3><div class="table-wrap"><table><thead><tr><th class="l">When</th>'
        '<th class="l">Video</th><th class="l">Change</th><th class="l">By</th><th class="l">Note</th>'
        "</tr></thead><tbody>"
        + (ev_rows or '<tr><td class="l" colspan="5">No activity yet.</td></tr>')
        + "</tbody></table></div></div>"
        '<div class="panel"><h3>Failed jobs</h3><div class="table-wrap"><table><thead><tr><th class="l">When</th>'
        '<th class="l">Job</th><th class="l">Video</th><th class="l">Detail</th></tr></thead><tbody>'
        + (fail_rows or '<tr><td class="l" colspan="4">None.</td></tr>')
        + "</tbody></table></div></div></div></section>"
    )

    # --- research -----------------------------------------------------------------------------
    rs = snap["research"]
    niche_bars = [(f"{n['niche']} → {n['channel']}", float(n["score"]) * 100, "") for n in rs["niches"]]
    parts.append(
        '<section id="research" class="panel"><h2>Research</h2>'
        f'<p class="note">Niche ranking from <code>{esc(rs["niche_file"] or "no run yet")}</code> '
        f"({esc(rs['niche_mode'] or '–')}). Velocity: {rs['accelerating_videos']} accelerating videos "
        f"(<code>{esc(rs['velocity_file'] or 'no run yet')}</code>). {esc(rs['velocity_note'])}</p>"
        f"{hbars(niche_bars, label_w=260)}"
        '<p class="note">Names come from YouTube research data and are shown as plain text.</p></section>'
    )

    # --- method -----------------------------------------------------------------------------------
    parts.append(
        '<section id="method" class="panel"><h2>How the scores work</h2>'
        f"<p><b>Video Performance Index (VPI, 0-100).</b> Every video is measured {scoring['snapshot_hours']} h after "
        "publishing, so an old video can't win just by being old. Each indicator the recommender rewards (views, "
        "click-through rate, average % viewed, % still watching at 30 s, likes+comments per view) is compared with "
        "the median of the same channel's other videos in the same format: log₂(value ÷ median), capped at 8× either "
        "way. The comparisons are weighted per format (Shorts ignore CTR) and averaged. Videos with few impressions "
        "are pulled toward 50 until the data is trustworthy. A logistic curve maps the result to 0-100: 50 is your "
        "typical video, about 80 means roughly twice as good on everything, about 20 means half.</p>"
        "<p><b>Channel score (50 = holding steady).</b> 50 % reach growth: are newer uploads getting more "
        f"{scoring['snapshot_hours']} h views than older ones (2× = 75, ½ = 25)? This is the part that shows the "
        "algorithm giving a channel more distribution, because VPI is relative to the channel itself. Plus 30 % "
        "recent form (VPI weighted toward the newest videos) and 20 % trend (robust slope of VPI across uploads). "
        "<b>Portfolio score</b> = channel scores weighted by how many scored videos back them.</p>"
        "<p><b>What works.</b> Each video's experiment arm (niche, format, hook, length, visual mix) is split up, "
        "and each choice gets its average VPI, shrunk toward the overall mean so small samples can't fool you.</p>"
        '<p class="note">Weights and thresholds live in <code>config/rules.yaml: scoring</code>.</p></section></main>'
    )

    body = "".join(parts)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta http-equiv="Content-Security-Policy" content="{esc(csp_for(CSS))}">'
        '<meta name="referrer" content="no-referrer"><meta name="robots" content="noindex">'
        f"<title>Portfolio dashboard</title><style>{CSS}</style></head><body>{body}</body></html>"
    )
