# Changelog

All notable changes to this project are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed
- More natural narration (owner feedback, ADR-031). Each segment is voiced in one continuous Kokoro pass. Word timings come from the timestamped Kokoro export's phoneme durations, which makes captions more accurate. Sentence gaps still follow our pause model. The voice chain is lighter, with room tone under pauses. Channel voices: atlas `am_fenrir`, neon `af_heart`, both at speed 1.0.
### Added
- `learn plan --channel <ch>`: next video formulas by factored Thompson sampling over niche × hook × length × visual mix, with research-informed niche priors (ADR-034). Scripts declare `niche` and `hook`; `src/learning/arms.py` derives the arm and `db set-script` stores it. Production refines the length bucket from the real narration.
- Word anchors (`at_word`) on diagram nodes/edges and on map markers, moves and routes, so diagrams and maps build in sync with the narration.
- Quality check `all_glyphs_render`: the text recorder notes characters the font can't draw (they render as empty boxes). This caught "→" in the atlas display font on the first batch.
- `/write-batch` renders a preview (`output/dev/<id>/`) and runs the quality checks before a script reaches the owner; `review show` prints the formula and the preview path, and warns when the preview is older than the script.
- First real batch: 4 Shorts (2 per channel) in the review queue, previews in `output/dev/<video_id>/`.
- Portfolio dashboard (`bash scripts/task.sh dashboard`, ADR-033). Tracks accounts, every video and its 48 h indicators, the pipeline, schedule, research, quota, cross-post exports, bandit and retention penalties. Success scoring: Video Performance Index per video, channel score with reach growth, portfolio score, and "what works" levers. Static, script-free HTML with a hash-pinned CSP; read-only DB; JSON for agents. Rebuilt by `/analyze` and `/weekly-brief`.

### Fixed
- Engine crash: skia's `TrimPathEffect.Make` returns null for near-empty trims, so a diagram arrow's first animation frame could kill a render (found on the first real batch). Now guarded, with a regression test.
- Captions were centered on the frame, but the Shorts safe zone is offset left of the right-edge buttons, so long caption lines crossed into the button zone. They now center on the safe area.
- The contrast check compared box-style captions' active word (dark text on an accent box) with the dark outline and reported 1.03:1; box mode now compares with the box.
- `db set-script` keeps the video's DB title in sync with the script's first title.
- The writing check's hook test looked for digits in the *spoken* text, so number hooks written as `{73|seventy-three}` failed. It now checks the on-screen text.
- Length estimates use each channel's measured narration rate (`writing.words_per_second`: atlas/am_fenrir 2.5, neon/af_heart 2.8); one shared rate put scripts in the wrong length bucket.
- Real videos never got a bandit arm, so the learning loop could not learn from real uploads (ADR-034).
- The distinctness guard never had history (nothing called `register`). `db set-script` now checks every script against all non-rejected videos, refuses a veto, and fingerprints it.
- Niche scoring ranked a niche with 2.6k median views first. It now uses percentile ranks for heavy-tailed evidence and has an explicit demand term.
- Velocity radar used one elapsed time for every video; each video is now timed against its own previous snapshot.
- Velocity radar re-run within the 24 h API cache window reused cached view counts and stored them as a new snapshot (zero acceleration, corrupt history). Snapshots now force fresh `videos.list` stats (`max_age_hours`), about 20 units per run; searches still come from the cache.
- A script could be attached to a video of another channel or format; it would then be rendered in the wrong style and uploaded with the wrong channel's token. `db set-script` now validates the script and requires a matching channel/format; `produce --video-id` re-checks; uploads use the video's channel and refuse a mismatched script.
- `quality --video-id` judged whatever `--video/--spec/--script` the caller passed; it now takes the recorded render, spec, audio and the **approved** script from the database (extra paths are refused) and refuses a render that changed while it was being checked.
- Thumbnails were never rendered for real videos (no path from a script's concepts to the engine) and never uploaded. Script thumbnail concepts now carry a renderable `focal` + `highlight`; production renders them into `thumbnails/` with legibility results; long-form uploads set the first legible one.
- Agents could not finish a script: the scriptwriter never ran `db set-script` (approval then fails with "no script attached") and headless permissions didn't allow it. Fixed in the agent, `/write-batch` and `settings.headless.json`.
- The visual-director was told to edit *approved* scripts, which invalidates the approval hash; it now works before review (`produce --dry-run`), matching `/write-batch`.
- Uploading after the scheduled slot passed would fail at YouTube; `publish` now refuses with the reschedule command, and rescheduling no longer counts the video's own old slot as taken.
- `output_path` is stored repo-relative (it was an absolute in-container path).
- Tests left scripts and renders in the real `data/scripts/` and `output/videos/`; a session fixture now removes what each test run creates.
- Demo description said "Sources in the pinned comment" although the sources are in the description.

### Security
- Closed the ADR-030 low-severity items: research JSON carries an untrusted-data notice (LLM01); the unpinned CI `pip` upgrade is removed (SSDF PW.4); explicit `--db` paths are confined to `data/` or the temp directory. The video ID YouTube returns and the thumbnail report names are validated before use (ADR-032).

### Changed (cont.)
- `scripts/fetch_assets.py` also downloads `kokoro-v1.0-timestamped.onnx` from Hugging Face (pinned commit; checksum pinned before download).

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
