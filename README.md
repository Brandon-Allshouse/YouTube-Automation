# YouTube-Automation: faceless video portfolio pipeline

An automated, self-built pipeline for a portfolio of faceless YouTube channels (Shorts +
long-form, with TikTok/Reels exports). We own the stack: a motion-graphics renderer (maps with
projection morphs and true-size moves, charts, timelines, diagrams, kinetic type), word-timed
captions, a thumbnail engine with phone-size legibility checks, procedural music, a mastering
chain, research and learning systems. Claude Code agents do the thinking (research, scripts,
critique, fact-checks, visual direction); deterministic Python does the work. Everything runs
locally in hardened containers at $0.

**Human review gate:** nothing is produced for publishing until the owner approves the script,
and nothing is uploaded except by the owner (`--live`, private by default). Both rules are enforced in code.

> Status and the morning summary: [PROGRESS.md](PROGRESS.md).

## Demo
`output/demo/mercator-short.mp4` (generated, gitignored): "Greenland looks as big as Africa, but it
isn't even close." It runs 57 s at 1080x1920 with a synthetic voice, word captions, a cinematic bed, map
animations, -14 LUFS, and passes every automated quality check. Its 16:9 twin is `mercator-16x9.mp4`;
thumbnails are in `output/demo/thumbnails/`; the review log is [output/demo/REVIEW.md](output/demo/REVIEW.md).

## Quick start (Docker, recommended)
Requirements on the host: **Docker** and **git** (and Git Bash on Windows). Nothing else is installed.

```bash
bash scripts/task.sh build          # build the runtime + dev images
bash scripts/task.sh fetch-assets   # OFL fonts, Natural Earth, Kokoro weights (checksum-verified)
bash scripts/task.sh test           # fast test suite (offline container)
bash scripts/task.sh render-demo    # = python -m src.production.produce --demo → output/demo/
```
With `make` available, `make <target>` is identical (`make help` lists everything; pass extra
arguments with `ARGS="..."`).

### Venv fallback (only if Docker is unavailable)
```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r requirements.lock && pip install imageio-ffmpeg
python -m scripts.fetch_assets && python -m src.production.produce --demo
```
FFmpeg then comes from `imageio-ffmpeg`, never a host install (ADR-002). Note that the TTS
library needs to `dlopen` espeak-ng from a temp dir that allows execution.

## Targets (`bash scripts/task.sh <target>`)
| Target | What it does |
|---|---|
| `build`, `lock`, `fetch-assets` | Images, hashed lockfiles, verified asset downloads |
| `test`, `test-all`, `coverage` | Tests (fast / incl. renders / with coverage) |
| `lint`, `format`, `typecheck` | ruff, ruff format, mypy --strict |
| `security` | Full scan suite → `security/REPORT.md` inputs |
| `render-demo`, `produce ...` | Production pipeline (`--demo`, `--script`, `--dry-run`, gated `--video-id`) |
| `quality ...`, `inspect ...` | Automated quality checks; contact sheets/key frames |
| `thumbnails ...` | Thumbnails + legibility reports |
| `research <module> [--live]` | outlier_scan, velocity_radar, comment_miner, niche_score |
| `analytics <module>`, `learn ...` | pull_stats, retention_forensics, weekly_brief; bandit |
| `distinctness ...`, `review ...` | Near-duplicate guard; human review queue |
| `timing ...`, `export ...` | Upload-slot optimizer; TikTok/Reels packages |
| `publish ...`, `oauth ...` | **Owner only**: upload (dry run unless `--live`), one-time OAuth |
| `db ...` | SQLite CLI used by agents |

## Layout
```
engine/      our renderer: core (easing, timeline, canvas, scene graph, geo, text, render), components, schema
src/         audio, research, production, compliance, review, publish, analytics, learning, quality, common, db.py
config/      channels/, themes/, rules.yaml, seed_niches.yaml, demo/
.claude/     agents/, commands/, settings.json (baseline), settings.headless.json (cron)
scripts/     task runner, asset fetcher, fixture generators, security scan
security/    REPORT.md, sbom.json, checksums.txt
tests/       pytest suite + fixtures (synthetic API responses, scene specs)
data/ output/ logs/ models/   runtime (gitignored)
```

## Documentation
- [PROGRESS.md](PROGRESS.md): status, morning summary, handoff
- [DECISIONS.md](DECISIONS.md): architecture decision records
- [SECURITY.md](SECURITY.md) · [security/REPORT.md](security/REPORT.md): threat model, scan results
- [LICENSES.md](LICENSES.md): every dependency, model, font, dataset
- [CHANGELOG.md](CHANGELOG.md) · [COMMITS.md](COMMITS.md)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/ENGINE.md](docs/ENGINE.md) · [docs/AUDIO.md](docs/AUDIO.md) · [docs/AGENTS.md](docs/AGENTS.md) · [docs/OPERATIONS.md](docs/OPERATIONS.md) · [docs/LEARNINGS.md](docs/LEARNINGS.md)
