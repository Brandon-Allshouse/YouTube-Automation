# Changelog

All notable changes to this project are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Foundation: hardened multi-stage `Dockerfile` (digest-pinned base, hash-locked wheels, non-root), `docker-compose.yml` services (`dev`, `render`, `quality`, `research`, `security`, `fetch`, `lock`), `.dockerignore` allowlist, `scripts/task.sh` + `Makefile`.
- Hash-locked `requirements.lock` / `requirements-dev.lock` generated with pip-compile.
- Security helpers: `src/common/paths.py` (traversal-safe paths), `src/common/proc.py` (allowlisted, shell-free subprocess), `src/common/ffmpeg.py` (single FFmpeg resolver), `src/common/log.py` (JSON logs with secret redaction), `src/common/secrets.py`, `src/common/config.py` (validated `config/rules.yaml`).
- `src/db.py`: SQLite schema, per-video state machine with an audited, code-enforced human review gate, quota tracking, API cache, JSON CLI.
- GitHub: CI, CodeQL (`python` + `actions`), Dependabot (`pip`, `docker`, `github-actions`), all SHA-pinned and validated with check-jsonschema + actionlint.
- `pre-commit` config (local, containerized hooks; not installed), detect-secrets baseline.
- Docs: README, PROGRESS, DECISIONS, LICENSES, SECURITY, CHANGELOG, COMMITS, `src/CLAUDE.md`.
- Render engine core (`engine/core/`): easing library, keyframe tracks, color/WCAG math, skia canvas wrapper, scene graph, balanced text layout, parallel chunked renderer → FFmpeg (BT.709), CLI.
- Components: KineticTitle, WordCaptions (pop/box/karaoke), StatCounter, ListReveal, QuoteCard, ComparisonSplit, transitions (wipe/zoom/slide/glitch/fade/iris), themed backgrounds. Themes `atlas`, `neon`.
- Scene spec schema (pydantic) + exported JSON Schema.
- Audio: Kokoro per-sentence TTS with blended channel voices, caption alignment, procedural music (4 styles), DSP (EQ, de-esser, compressor, BS.1770 meter, TP limiter), mixer, analysis images.
- Channel configs `atlas`, `neon`; production orchestrator with word-anchored visuals and a gated production mode.
- Demo Short `mercator-short` (fact-checked script, sources cited).

- Step-6 components: AnimatedMap (Natural Earth, Mercator/Equal Earth/equirect projections with animated morphs, true-size sphere moves, great-circle and rhumb routes, markers), LineChartReveal, BarRace, Timeline, DiagramBuild, IconScene (12 code-drawn icons), ParticleBackground.
- Thumbnail engine (`src/production/thumbnail.py`) and phone-size legibility checker (`src/quality/legibility.py`).
- Claude Code agents (7), slash commands (6), baseline + headless permission files.
- Research: read-only YouTube Data API client with SQLite quota cap + cache, outlier scan, velocity radar, comment miner, niche scorer, seed niches, synthetic fixtures; own TF-IDF similarity and untrusted-text handling.
- Distinctness guard, interactive human review queue, TikTok/Reels export, timing optimizer (DST-correct, cadence caps, jitter), owner-only private-by-default upload + PKCE OAuth.
- Analytics/learning: stats pull (fixtures), Beta-Bernoulli bandit, retention forensics, ridge predictor (gated), topic graph, weekly brief.
- Automated quality checks (`src/quality/run.py`): safe zones, words on screen, WCAG contrast on real pixels, static frames, loudness/peaks/silence, writing rules.
- Security report (`security/REPORT.md`), CycloneDX SBOM, operations runbook (`docs/OPERATIONS.md`).

### Changed
- Demo Short polished with map scenes (Mercator hook, true-size slide, rhumb line, projection morph); 16:9 render from the same spec.
- Content regions shrink through the camera push-in so text never drifts out of the safe zone; map framing is format-independent; map labels clamp to the safe area.
- ruff line length 100 → 120.
