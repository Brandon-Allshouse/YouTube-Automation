# Architecture Decision Records

Format: number, date, context, decision, alternatives, consequences. Every dependency and every
significant design choice gets an ADR. Newest at the bottom.

---

## ADR-001: Docker-first execution
- **Date:** 2026-10-15
- **Context:** CLAUDE.md requires nothing installed on the host. `docker info` succeeded (Docker 29.8, Linux VM, amd64, 24 CPU, 16 GB).
- **Decision:** All builds, tests, renders and scans run in containers defined by `Dockerfile` + `docker-compose.yml`. The `.venv` fallback is supported by code (single FFmpeg resolver) but was **not used tonight**.
- **Alternatives:** project-local `.venv` + `imageio-ffmpeg` (kept as fallback only).
- **Consequences:** Reproducible, isolated environment; the host needs only Docker + git. Source is bind-mounted read-only per directory, so code edits don't need image rebuilds.

## ADR-002: One FFmpeg resolver
- **Date:** 2026-10-15
- **Decision:** `src/common/ffmpeg.py::ffmpeg_exe()` is the only way code finds FFmpeg: the image's `/usr/bin/ffmpeg` in containers, otherwise `imageio_ffmpeg.get_ffmpeg_exe()`. A host FFmpeg is never used.
- **Consequences:** Mode-agnostic code; Debian bookworm FFmpeg 5.1 in Docker mode.

## ADR-003: Base image `python:3.11-slim-bookworm`, pinned by multi-arch index digest
- **Date:** 2026-10-15
- **Decision:** `FROM python:3.11-slim-bookworm@sha256:0a310eee…f5f89` (the OCI index digest, so the same pin works on amd64 and arm64). Dependabot's `docker` ecosystem updates the digest.
- **Alternatives:** Alpine (musl breaks skia/onnxruntime wheels), distroless (no FFmpeg package manager), 3.12/3.13 (CLAUDE.md asks for 3.11; all deps have 3.11 wheels).
- **Consequences:** ~1.4 GB runtime image (FFmpeg + onnxruntime + skia dominate).

## ADR-004: Image layout and hardening
- **Date:** 2026-10-15
- **Decision:** Four stages: `builder` (hash-locked wheels only, `--only-binary=:all:` so no compilers anywhere), `builder-dev`, `runtime` (non-root UID/GID 1000, FFmpeg + skia shared libs), `dev` (runtime + QA tooling + git). Compose services all use `read_only`, `cap_drop: [ALL]`, `no-new-privileges`, CPU/mem/PID limits, `init`, tmpfs `/tmp`. `dev`/`render`/`quality` are `network_mode: none`; only `research`, `security` (pip-audit), `fetch` (asset downloads) and `lock` have network. Only `research` gets `env_file: .env`.
- **Notable detail:** a whole-repo bind mount would expose `.env` inside every container. Services therefore mount individual directories; the two whole-repo services (`security`, `lock`) mask `.env` with `/dev/null` (verified: 0 bytes inside the container).
- **Alternatives:** copying source into the image (requires rebuild per edit; can be added for production later).

## ADR-005: Task runner = `scripts/task.sh`, Makefile delegates
- **Date:** 2026-10-15
- **Context:** The host (Windows) has no `make`; installing it would violate the host rule.
- **Decision:** `scripts/task.sh <target>` is the single definition; `Makefile` forwards every target to it. Works under Git Bash, macOS and Linux.

## ADR-006: Runtime dependency set (primitives only)
- **Date:** 2026-10-15
- **Decision:** Runtime deps are `numpy`, `scipy` (DSP filter primitives), `skia-python` (2D drawing), `pyyaml` (config, `safe_load` only), `pydantic` (validation of scene specs/config/API responses; also generates the scene-spec JSON Schema, so no separate `jsonschema` runtime dep), `kokoro-onnx` (Kokoro TTS inference). Everything else (renderer, captions, music, mixer, research, learning, distinctness) is ours.
- **Not used:** pandas (not needed yet), scikit-learn (our own TF-IDF is small; ADR to follow if we change), requests (stdlib `urllib` suffices for read-only API GETs), Pillow (skia encodes/decodes PNG).
- **Locking:** `pip-compile --generate-hashes` → `requirements.lock` / `requirements-dev.lock`; image installs with `--require-hashes`.

## ADR-007: Kokoro via `kokoro-onnx` (ONNX Runtime) instead of the PyTorch `kokoro` package
- **Date:** 2026-10-15
- **Context:** CLAUDE.md specifies Kokoro TTS (Apache-2.0 weights). The reference package pulls PyTorch (~700 MB+ CPU wheels, extra index for CPU-only builds, harder hash locking).
- **Decision:** Use `kokoro-onnx` (MIT) with the official Kokoro v1.0 ONNX export, running on `onnxruntime` (MIT). Phonemization uses `phonemizer` + `espeakng-loader`, which bundles the espeak-ng library and data, so no `apt install espeak-ng` is needed.
- **Alternatives:** PyTorch `kokoro` (heavier); Piper (fallback if Kokoro fails).
- **License note:** `phonemizer` and the bundled espeak-ng are **GPL-3.0-or-later**. We use them unmodified as local tools and never redistribute them in the repo; the generated audio is not a derivative work of the tool. If the owner ever ships the Docker image publicly, GPL source-offer obligations would apply to those components. Recorded in LICENSES.md.

## ADR-008: Pre-commit hooks are `repo: local` and run in containers
- **Date:** 2026-10-15
- **Decision:** Hooks call `scripts/task.sh`/`docker compose run`, so no hook environments install on the host and no third-party hook repos need pinning. Hooks are **not installed** (owner decides: `pre-commit install`).

## ADR-009: GitHub Actions pinned to verified SHAs; CodeQL Action v3
- **Date:** 2026-10-15
- **Decision:** SHAs resolved via the GitHub API (annotated tags dereferenced to commits): `actions/checkout` v7.0.1 `3d3c42e5…`, `actions/setup-python` v7.0.0 `5fda3b95…`, `github/codeql-action` v3.38.3 `9f759ee6…` (released 2026-10-08). CodeQL languages `python` + `actions`, `build-mode: none`, suites `security-extended,security-and-quality`.
- **Note:** CodeQL Action v4 (v4.38.3) is also current. CLAUDE.md asks for v3, which is still maintained; Dependabot (github-actions ecosystem) will propose the major bump as a separate, ungrouped PR.

## ADR-010: SQLite state machine with code-level human review gate
- **Date:** 2026-10-15
- **Decision:** `src/db.py` owns the schema. Video states `idea → scripted → reviewed → voiced → specd (spec'd) → rendered → checked → scheduled → published`, plus `failed`/`rejected` and explicit rework loops. Entering `reviewed` requires a named human approver; actors/approvers named `agent`, `claude`, `bot`, `cron` are refused; `scheduled`/`published` require a prior approval; a revision clears the approval. Every transition is audited in `video_events`. Parameterized SQL only; the one dynamic column update uses a fixed allowlist → fixed SQL map.

## ADR-011: Renderer architecture: chunked parallel workers, each with its own FFmpeg encoder
- **Date:** 2026-10-15
- **Context:** Raw 1080×1920 RGBA frames are ~8 MB; shipping them between processes to one encoder bottlenecks on IPC.
- **Decision:** Split frames into contiguous chunks (3 × workers); each worker builds the composition from plain args (spec JSON), renders into a numpy-backed skia surface and pipes frames to its own x264 encoder. Segments are joined with the concat demuxer (`-c copy`), then audio is muxed with `+faststart`. Explicit BT.709 conversion + tags.
- **Alternatives:** single encoder fed by an ordered pool (IPC-bound); writing PNG frames to disk (slow, huge).
- **Consequences:** ~1.4-1.5× realtime at 1080p30 on the build machine. Segment boundaries start with IDR frames (negligible size cost).

## ADR-012: Single drawing interface (`engine/core/canvas.py`); pycairo fallback not implemented
- **Date:** 2026-10-15
- **Decision:** Components draw only through `Canvas`/`PathBuilder`/`Fonts`/`Picture`; skia is imported nowhere else. skia-python publishes cp311 wheels for both linux x86_64 and aarch64 (verified on PyPI), so the pycairo backend CLAUDE.md allows as a fallback is not needed today. If a platform lacks wheels, a pycairo implementation of the same interface is the seam.

## ADR-013: Scene spec = pydantic models; JSON Schema generated from them
- **Date:** 2026-10-15
- **Decision:** `engine/schema/spec.py` defines strict, bounded models with a `type` discriminator per component. `scene_spec.schema.json` is exported from the models (test keeps it in sync) and given to the `visual-director` agent. This replaces a separate `jsonschema` runtime dependency.
- **Consequences:** One source of truth; validation errors are precise; hostile agent output (unknown fields, huge values, path-like ids, bad easings) is rejected (tests cover these).

## ADR-014: Two launch themes, `atlas` and `neon`
- **Date:** 2026-10-15
- **Decision:** `atlas` (history/geography): ink + parchment + brass, Fraunces serif display, Inter captions in `pop` style, paper/hairline background, angled wipes, slower expo motion. `neon` (science/tech): navy-black + cyan/magenta, Space Grotesk, uppercase `box` captions, grid + perspective floor, glitch transitions, snappy springs. Both pass WCAG AA for text and muted text on every background color (tested).
- **Fonts (OFL):** Inter, Fraunces, Space Grotesk, JetBrains Mono, Anton; pinned to google/fonts commit `b7c8331c…`, checksummed.

## ADR-015: Own DSP and loudness metering; FFmpeg only as an independent cross-check
- **Date:** 2026-10-15
- **Context:** CLAUDE.md allows FFmpeg `loudnorm` as a primitive. Two-pass loudnorm is a black box and its dynamic mode can pump.
- **Decision:** Implement BS.1770-4 integrated loudness, true-peak (4× oversampling) and a lookahead limiter ourselves; normalise with linear gain + limiter; verify against FFmpeg `ebur128` in analysis.
- **Consequences:** Deterministic, testable mastering (reference-tone test). Matches FFmpeg within 0.02 LU on test mixes.

## ADR-016: Caption timing by syllable weighting + detected pauses
- **Date:** 2026-10-15
- **Context:** Kokoro v1.0 ONNX exposes no word timings (`has_timings == False`, single `audio` output).
- **Decision:** Per-sentence audio + our alignment (speech span, pause↔clause matching, syllable-weighted distribution). Forced alignment deferred until reviews show drift.

## ADR-017: Exec-allowed tmpfs only for espeak-ng dlopen
- **Date:** 2026-10-15
- **Context:** phonemizer copies `libespeak-ng.so` into `tempfile.mkdtemp()` and dlopens it; Docker tmpfs is `noexec`, so loading fails with "failed to map segment".
- **Decision:** Keep `/tmp` noexec; add a 32 MB `exec` tmpfs at `/var/tmp/dlopen` and point `tempfile.tempdir` at it only while Kokoro initialises (`src/audio/tts._dlopen_tmp`).
- **Alternatives:** making `/tmp` exec (broader); installing system espeak-ng (phonemizer still copies the library).

## ADR-018: Channel identity files (`config/channels/*.yaml`)
- **Date:** 2026-10-15
- **Decision:** Each channel defines theme, synthetic voice blend + speed, music style + seed + level, writing persona + banned phrases, length ranges and cadence (validated by `src/common/channels.py`). Launch channels: `atlas` (history/geography, cinematic, British blend) and `neon` (science/tech, synthwave, American blend).

## ADR-019: Word-anchored visuals and display/spoken markup in scripts
- **Date:** 2026-10-15
- **Decision:** Script segments carry visual elements that may reference spoken words (`at_word`, `until_word`, `count_at_word`, `reveal_words`, `word#n` for the n-th occurrence); production resolves them to scene-relative times after TTS. `{display|spoken}` lets captions show "1569"/"60°" while the voice says "fifteen sixty-nine"/"sixty degrees".
- **Why:** Animation beats land exactly on the narration without the agent guessing timings, and one segment can hold several visual beats (keeps every shot under the 4 s static bar).

## ADR-020: Honest-scale comparisons
- **Date:** 2026-10-15
- **Decision:** `ComparisonSplit` defaults to area-true squares (side ∝ √value) for areas; bars only for linear quantities. Misleading scale would violate the "no misleading visuals" principle.

## ADR-021: Maps from Natural Earth GeoJSON with our own projection math
- **Date:** 2026-10-15
- **Decision:** Parse Natural Earth admin-0 GeoJSON (public domain, pinned commit, checksummed) with `json`; implement Mercator, Equal Earth (Šavrič et al. 2018) and equirectangular projections, spherical area and rotation ourselves. No GIS dependency (shapely/pyproj/geopandas).
- **Signature features:** animated projection morphs and "true size" moves (rigid rotation on the sphere, then re-projection) for honest size comparisons.
- **Verified:** spherical areas match references (Greenland 2.15 vs 2.17 M km², Brazil 8.50 vs 8.52, Africa 30.05 vs 30.37); property tests prove rotation preserves area (after an antimeridian unwrap fix found by Hypothesis) and Equal Earth is equal-area.

## ADR-022: Thumbnail engine + measured legibility gate
- **Date:** 2026-10-15
- **Decision:** Thumbnails (1280×720) are rendered by our engine: one focal visual (true-size silhouettes, Mercator crop with true-size ghost, stat, or icon) + ≤ 4 words on a left scrim, highlight word in a solid box. `src/quality/legibility.py` measures: cap height ≥ 9 px at 168 px wide and ≥ 18 px at 360 px; WCAG contrast of text vs the text-free plate (median ≥ 4.5, worst ≥ 3) or vs the known box fill; nothing in the duration-badge corner. A phone preview image is saved for human review.
- **Fonts:** themes may set a `thumb` font role (atlas: Anton, a condensed face that stays legible at 168 px).
- **Honesty:** silhouettes use a north-up sphere slide + equal-area projection; copy must not personify or exaggerate ("Greenland lied" → "Your map lies").

## ADR-023: Two-layer Claude Code permissions
- **Date:** 2026-10-15
- **Context:** CLAUDE.md asks `.claude/settings.json` to scope headless runs to least privilege. Project settings also apply to every interactive session (the owner's and this build session; a strict file was observed to take effect live).
- **Decision:** `.claude/settings.json` holds the safe baseline for all sessions (secret reads, publishing, approval, network-mutating and destructive commands denied). `.claude/settings.headless.json` holds the strict allowlist; cron runs use `claude -p "/cmd" --settings .claude/settings.headless.json`, and deny lists merge, so headless runs get the union.
- **Consequences:** Owner dev sessions stay usable; headless agents are confined to `scripts/task.sh` entry points and `data/`. Gates are additionally enforced in code.

## ADR-024: Own research stack on the read-only Data API; fixtures first
- **Date:** 2026-10-15
- **Decision:** stdlib `urllib` HTTPS GETs (no `google-api-python-client`), allowlisted endpoints (search, videos, channels, commentThreads), pydantic models keeping only needed fields, quota checked in SQLite before every call against `session_cap_units` (8,000), 24 h response cache shared by all modules. Every module runs on deterministic synthetic fixtures by default; `--live` is opt-in. Text similarity (TF-IDF + char n-grams, greedy clustering) is ours (`src/common/textsim.py`), not scikit-learn.
- **Metric decisions after the live run:** supply uses log10(totalResults) because the API's totalResults is a saturating estimate; outlier density is reported separately for long-form and Shorts (≤ 180 s) and weighted 70/30 in the niche score because Shorts routinely out-view subscriber counts.

## ADR-025: Line length 120
- **Date:** 2026-10-15
- **Decision:** ruff `line-length = 120` (lint + format). Dense numeric/rendering code at 100 produced many awkward wraps; 120 is a common standard and is enforced uniformly by the formatter.

## ADR-026: Human approval requires an interactive confirmation in code
- **Date:** 2026-10-15
- **Decision:** `src/review/queue.py approve` refuses unless stdin is a TTY, the reviewer name is not an agent actor, and the reviewer types the video id back. `src/db.transition` independently refuses agent actors. Headless agents are also denied the command in `.claude/settings*.json`. Three independent layers for the most important gate.

## ADR-027: Publishing = private upload with publishAt; owner-run only
- **Date:** 2026-10-15
- **Decision:** Uploads go up `private` with `status.publishAt` = the jittered slot, so YouTube itself flips them public on schedule and nothing needs to run at publish time. `containsSyntheticMedia` is always set explicitly (official field since 2024-10-30; omission is silent) and `selfDeclaredMadeForKids=false`. OAuth is stdlib PKCE with manual loopback paste (works in containers, no open ports), scopes `youtube.upload`, `youtube.readonly`, `yt-analytics.readonly`; tokens outside the repo (mode 600). A dedicated `publish` compose service is the only place the secrets directory is mounted.
- **Cadence:** ≤ 1 Short and ≤ 1 long-form per channel per local day, ≤ `long_per_week` long-form in any rolling 7 days, ≥ 45 min between any two uploads across the portfolio, seeded jitter.

## ADR-028: Learning signals that can't be gamed by clickbait
- **Date:** 2026-10-15
- **Decision:** Bandit success requires beating the channel median on 48 h views **and** not falling below the channel median average-view %. Retention forensics compares each segment's loss rate to the same video's median rate (controls for topic/length), aggregating a per-visual-type penalty table. The predictor (ridge, NumPy) refuses to fit below 100 videos. All are validated on synthetic fixtures with planted signal.

## ADR-029: Quality checks measure real renders; transient vs persistent
- **Date:** 2026-10-15
- **Decision:** Text checks use an opt-in canvas recorder (device-space boxes after all transforms, effective opacity) plus a text-suppressed second render for true background contrast. Word-count and contrast violations must persist across two consecutive samples (≥ 0.5 s) to fail, since crossfades last ~0.3 s; both transient and persistent peaks are reported. Safe-zone and off-canvas violations are never filtered. Content regions are pre-shrunk through the camera push-in transform so drift can never carry text out of the safe zone; scene drift overrides may only reduce drift.

## ADR-030: Approval and quality gates bound to content hashes (independent review)
- **Date:** 2026-10-15
- **Context:** An independent review subagent (read-only) found that the human approval was not tied to the script that gets produced or the file that gets uploaded, and that headless agents had paths to arbitrary code execution (pytest pass-through of agent-written files, unrestricted Write/Edit in two slash commands) and to OAuth-token reads.
- **Decision:**
  - Approval stores the script's SHA-256; `produce --video-id` renders only the recorded `script_path` and refuses if the hash changed. `set-script` is refused after approval; any return to drafting clears approval and fingerprints; `failed` can only retry via `idea`/`scripted`.
  - Only a **passing** `src.quality.run --video-id` moves `rendered → checked`, and it stores the render's SHA-256. Upload re-verifies both hashes and only accepts files under `output/videos/`, which ungated `produce` may not write.
  - The agent DB CLI can only set `scripted`/`rejected`/`failed`. Scheduling (`timing --schedule`) needs an interactive terminal.
  - Headless permissions: reads limited to the repo; token and credential paths denied; edits to `src/`, `engine/`, `scripts/`, `tests/`, `config/`, `.claude/` denied; test targets take only options and `tests/` paths (enforced in `scripts/task.sh`); slash commands write only under `data/`.
  - Log redaction covers JSON-style secrets and `GOCSPX-` client secrets; the OAuth dir is 0700; Unicode tag characters are stripped from untrusted text; thumbnail CLI paths are confined.
- **Open (lower severity):** untrusted strings inside research JSON are cleaned but not individually `<untrusted_data>`-wrapped (agent definitions treat all API fields as data); CI `pip==25.2` upgrade is not hash-pinned; hidden `--db` test options accept any path. *Closed by ADR-032.*

## ADR-031: Natural narration: whole-segment Kokoro with model word timings
- **Date:** 2026-10-15 (owner feedback: "the voice is too synthetic")
- **Context:** The demo voice blended `bm_george` + `bm_fable` (two of Kokoro's lowest-graded, little-data voices) at speed 1.1, synthesized one sentence at a time with digital silence between sentences. Each adds to the synthetic feel: a muddy style, squeezed timing, intonation that resets at every full stop, and no room sound.
- **Decision:**
  - Channel voices are single stock Kokoro voices at speed 1.0: atlas `am_fenrir` (owner's pick from an A/B of nine samples), neon `af_heart` (Kokoro's top-rated voice; keeps the channels distinct).
  - Each script segment is voiced in **one continuous pass** (kokoro-onnx `create_timed(continuous=True)`), so prosody runs across its sentences.
  - Word timings come from the model: the **timestamped ONNX export of the same Kokoro-82M v1.0 weights** (`onnx-community/Kokoro-82M-v1.0-ONNX-timestamped`, pinned commit `dd4401a9`; checksum taken from Hugging Face's LFS record before download, not trust-on-first-use) reports per-phoneme durations. `align_phoneme_words` matches each word's isolated pronunciation to the passage's phoneme groups with a small DP (edit distance, joins and splits), because espeak fuses words in context ("from the" → `fɹʌmðɪ`).
  - The passage is cut into sentence clips at the gaps; our pause model still sets each gap. Captions use the model's word times instead of energy alignment (more accurate).
  - Mix: lighter voice chain (presence +1.2 dB instead of +2.5, 2.2:1 with a slower attack) and a band-limited room-tone floor 54 dB under the voice, so pauses aren't digital silence.
  - Fallbacks: without the timestamped model, or when a passage can't be aligned, sentences are synthesized one at a time with energy alignment (logged as `word timing mismatch`).
- **Alternatives:** keep per-sentence synthesis (robotic prosody); infer sentence cuts from silence only (fragile); expose durations by editing the ONNX graph (needs the `onnx` package); switch to a heavier expressive TTS such as Chatterbox (MIT, PyTorch, > 1 GB, much slower on CPU), kept in reserve if the owner still finds Kokoro too synthetic.
- **Consequences:** no new Python dependency; one more 325 MB model file, fetched and verified like the others. TTS for the 55 s demo takes about 6 s. The old `kokoro-v1.0.onnx` stays as the fallback.

## ADR-032: Pipeline integrity fixes from an end-to-end run, and closing the ADR-030 lows
- **Date:** 2026-10-15
- **Context:** Gate unit tests passed, but running the real flow (`add-video → set-script → approve → produce --video-id → quality --video-id → timing --schedule → publish` dry run) showed integration gaps.
- **Decision:**
  - A script must validate and match its video's channel and format at `set-script` and again at production. Uploads use the token for the video's recorded channel and refuse a mismatched script.
  - `quality --video-id` takes the render, spec, audio and approved script from the database (caller paths are refused), and refuses a render that changed during the checks.
  - Thumbnails are part of the approved script (`focal`, `highlight`). Production renders them, and long-form uploads set the first legible one. Names and the returned YouTube ID are validated.
  - Publish refuses a private `publishAt` slot that has passed, and rescheduling ignores the video's own old slot. `output_path` is repo-relative.
  - ADR-030 lows: research JSON carries an `untrusted_fields_notice`; the unpinned CI `pip` upgrade is removed (all installs use `--require-hashes`); `db.connect` confines an explicit path to `data/` or the temp directory.
- **Alternatives:** wrap every JSON string in `<untrusted_data>` tags (rejected: it corrupts values that code consumes; cleaning plus a notice plus the agent definitions cover LLM01); keep caller paths in quality and only compare hashes (rejected: deriving from the database leaves nothing to compare wrongly).
- **Consequences:** agents need `db set-script` (allowed headless; it validates the script and only works before approval). `quality --video-id` takes no paths. Tests clean up what they create in `data/scripts` and `output/videos`.

## ADR-033: Portfolio dashboard and success scoring
- **Date:** 2026-10-15 (owner: "a dashboard that tracks EVERYTHING ... some algorithmic function to show success")
- **Decision:**
  - **Video Performance Index (VPI, 0-100):** every video is scored at the same age (48 h) against the leave-one-out median of its channel × format (priors from `rules.yaml` until 3 peers exist). The score combines clipped `log2(value/baseline)` over views, CTR, average view %, retention at 30 s and engagement, with per-format weights (Shorts ignore CTR) renormalized over the metrics present. It shrinks toward 50 by impressions/(impressions + 1,500) and is mapped through a logistic curve (2× everything ≈ 80).
  - **Channel score (50 = steady):** 50 % reach growth (newer vs older half of 48 h views, `50 + 25·log2`), 30 % recency-weighted form, 20 % Theil-Sen VPI trend. VPI alone is relative to the channel, so it can't show the algorithm growing a channel; reach growth does. **Portfolio score:** channel scores weighted by scored videos.
  - **Levers:** arm dimensions (niche, format, hook, length, visual) with empirical-Bayes shrunk mean VPI (prior strength 3); **bandit:** Beta 90 % intervals plus seeded Monte Carlo P(best).
  - **Delivery:** a static HTML page plus JSON for agents, generated by our own code (SVG charts, no chart or web framework). It reads the DB read-only, has no JavaScript and a hash-pinned CSP. The fixture mode is clearly labelled synthetic.
- **Alternatives:** a hosted or claude.ai page (rejected: private analytics would leave the machine, against "everything runs locally"); a local web server (rejected: more attack surface for no gain); a dashboard framework such as Streamlit or Grafana (rejected: product-level dependency); raw view counts as the score (rejected: confounded by video age and format); the research "median views" as an absolute benchmark (rejected: top search results are biased toward winners).
- **Consequences:** scores are meaningful once videos have 48 h stats; until then the page shows empty states. Metric names are platform-neutral, so other platforms' videos can be scored the same way later.

## ADR-034: Closing the learning loop: code-derived arms, factored Thompson sampling, robust niche scoring
- **Date:** 2026-10-15 (go-live prep; owner: "fully algorithmically optimal")
- **Context:** The go-live review found three gaps. (1) Real videos never received a bandit arm (`add-video` had no way to set one), so the bandit, predictor and dashboard levers could never learn from real uploads. (2) The distinctness guard's history was filled only by a `register` command that no workflow called, so it could never block a duplicate. (3) The niche scorer ranked "tech history" first on 2.6k median views, because min-max normalization let tiny competitor channels make its competition and gap terms look perfect.
- **Decision:**
  - **Arms from code (`src/learning/arms.py`):** scripts declare `niche` (a seed niche of their channel, checked) and `hook` (`question|statement|number|stakes|story`). Code computes the length bucket (`s35/s45/s55`, `l6..l14`; estimated at 2.5 spoken words/s, then set from the real narration length at production) and the visual mix (component categories weighted by narration words; ≥ 45 % names the mix). `db set-script` stores the arm.
  - **Factored Thompson sampling (`learn plan`):** each dimension value has its own Beta posterior over every outcome that shares it, and a slot takes the best draw per dimension. Niche priors come from research, as Beta(1 + 4s, 1 + 4(1 - s)) on the niche score s, and only each channel's top-4 researched niches are probed (CLAUDE.md: 4-8 niches). Slots are distinct.
  - **Distinctness in code:** `db set-script` runs the guard against every non-rejected video (drafts included) and refuses a veto, then fingerprints the script.
  - **Niche scoring:** heavy-tailed evidence (views, gap, subscribers, question counts) is ranked by percentile, and a demand term (median views, 0.15) is added. Weights: outlier density 0.20, demand 0.15, gap 0.15, RPM 0.15, visual fit 0.15, evergreen 0.08, competition 0.07, unanswered 0.05. The velocity radar now times each video against its own previous snapshot.
- **Alternatives:** an independent Beta per full arm (rejected: 300+ arms per channel, so it would never converge at one Short a day; the factored model is the step before the planned hierarchical model); agent-declared length and visual labels (rejected: an LLM can mislabel, so code derives them); registering fingerprints only at approval (rejected: two drafts in one batch could duplicate each other); keeping min-max normalization (rejected: one extreme value squashes every other niche to 0).
- **Consequences:** every new script must declare `niche` and `hook` to join the loop (unlabelled scripts still work, without an arm). The first real batch (4 Shorts) was written to `learn plan` formulas. Hook, length and visual effects are shared across channels, which assumes they transfer; the hierarchical upgrade at ~50 videos can relax that.
