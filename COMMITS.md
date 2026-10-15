# Suggested commits (for the owner)

The build agent never commits. The overnight build's 10 groups are **already committed** by the
owner (`40cc330 commit every file`); they're kept below for reference. Pending groups come first.
Before listing a group the agent checks that no file in it is gitignored (`git check-ignore`) and
that detect-secrets finds nothing. **`.env` is in no group.**

How to apply one group (Git Bash): copy its file list into `git add -- <files>`, then `git commit` with the message.

## Pending

The owner is committing session 2 as **one commit** (`git add -A`; 45 modified files, no new or
ignored files; `.env` and raw scan outputs are gitignored):

```
feat: natural narration, end-to-end pipeline fixes and security hardening
```
(full message in the session; after pulling, run `bash scripts/task.sh fetch-assets` once for the
new Kokoro timing model).

## Committed (reference)

---

## 1. `chore(repo): add hardened containers, tooling and security baseline`
Files (38):
```
.dockerignore
.env.example
.gitattributes
.github/codeql/codeql-config.yml
.github/dependabot.yml
.github/workflows/ci.yml
.github/workflows/codeql.yml
.gitignore
.pre-commit-config.yaml
.secrets.baseline
Dockerfile
Makefile
config/.env.example
config/rules.yaml
docker-compose.yml
pyproject.toml
requirements-dev.in
requirements-dev.lock
requirements.in
requirements.lock
scripts/__init__.py
scripts/fetch_assets.py
scripts/security_scan.sh
scripts/task.sh
security/checksums.txt
src/__init__.py
src/common/__init__.py
src/common/config.py
src/common/ffmpeg.py
src/common/log.py
src/common/paths.py
src/common/proc.py
src/common/secrets.py
src/db.py
tests/__init__.py
tests/conftest.py
tests/test_common.py
tests/test_db.py
```
Message:
```
chore(repo): add hardened containers, tooling and security baseline

Digest-pinned multi-stage Dockerfile with hash-locked wheels and a non-root user;
hardened compose services (read-only FS, cap_drop ALL, no network for render/test);
safe path/subprocess/logging/config helpers; SQLite state machine with a
code-enforced human review gate; CI, CodeQL and Dependabot with SHA-pinned
actions; asset fetcher with checksum verification.
```

---

## 2. `feat(engine): add motion-graphics renderer, component library and themes`
Files (46):
```
config/themes/atlas.yaml
config/themes/neon.yaml
engine/CLAUDE.md
engine/__init__.py
engine/build.py
engine/cli.py
engine/components/__init__.py
engine/components/ambient.py
engine/components/background.py
engine/components/base.py
engine/components/captions.py
engine/components/charts.py
engine/components/comparison.py
engine/components/diagrams.py
engine/components/extra.py
engine/components/kinetic_title.py
engine/components/list_reveal.py
engine/components/maps.py
engine/components/quote_card.py
engine/components/stat_counter.py
engine/components/transitions.py
engine/core/__init__.py
engine/core/canvas.py
engine/core/color.py
engine/core/easing.py
engine/core/geo.py
engine/core/layout.py
engine/core/render.py
engine/core/scene.py
engine/core/text.py
engine/core/theme.py
engine/core/timeline.py
engine/demo_core.py
engine/schema/__init__.py
engine/schema/scene_spec.schema.json
engine/schema/spec.py
engine/themes/__init__.py
src/quality/__init__.py
src/quality/frames.py
src/quality/inspect.py
tests/fixtures/specs/components_demo.json
tests/fixtures/specs/map_demo.json
tests/fixtures/specs/showcase.json
tests/test_components.py
tests/test_components_extra.py
tests/test_engine_core.py
```
Message:
```
feat(engine): add motion-graphics renderer, component library and themes

Easing/keyframes/timeline, skia canvas wrapper, scene graph, balanced text layout,
parallel chunked rendering to FFmpeg (BT.709), Natural Earth geo + projections;
components incl. kinetic titles, captions, stats, lists, quotes, comparisons,
maps (projection morph, true-size moves), charts, timelines, diagrams, icons,
particles; validated scene-spec schema; atlas and neon themes.
```

---

## 3. `feat(audio): add Kokoro TTS, caption timing, procedural music and mastering`
Files (11):
```
config/channels/atlas.yaml
config/channels/neon.yaml
src/audio/__init__.py
src/audio/analyze.py
src/audio/captions.py
src/audio/dsp.py
src/audio/mixer.py
src/audio/music.py
src/audio/tts.py
src/common/channels.py
tests/test_audio.py
```
Message:
```
feat(audio): add Kokoro TTS, caption timing, procedural music and mastering

Per-sentence Kokoro synthesis with blended channel voices and natural pauses,
syllable/pause-based caption alignment, NumPy music generator (4 styles), voice
chain, ducking, BS.1770 loudness and true-peak limiting; channel configs.
```

---

## 4. `feat(production): add produce pipeline, demo Short and thumbnail engine`
Files (9):
```
config/demo/mercator_short.yaml
output/demo/REVIEW.md
src/production/__init__.py
src/production/produce.py
src/production/script.py
src/production/thumbnail.py
src/quality/legibility.py
tests/test_production.py
tests/test_thumbnail.py
```
Message:
```
feat(production): add produce pipeline, demo Short and thumbnail engine

Script model with word-anchored visuals, gated production orchestrator, fact-checked
Mercator demo script, thumbnail engine with phone-size legibility checks.
```

---

## 5. `feat(agents): add subagents, slash commands and least-privilege permissions`
Files (19):
```
.claude/agents/analyst.md
.claude/agents/audience-panel.md
.claude/agents/compliance-guard.md
.claude/agents/fact-checker.md
.claude/agents/researcher.md
.claude/agents/scriptwriter.md
.claude/agents/visual-director.md
.claude/commands/analyze.md
.claude/commands/daily-research.md
.claude/commands/produce.md
.claude/commands/publish-due.md
.claude/commands/weekly-brief.md
.claude/commands/write-batch.md
.claude/settings.headless.json
.claude/settings.json
docs/AGENTS.md
```
Message:
```
feat(agents): add subagents, slash commands and least-privilege permissions

Seven subagents (model: inherit, prompt-injection rules), six slash commands,
baseline settings.json and strict settings.headless.json for cron runs.
```

---

## 6. `feat(research): add quota-capped YouTube research modules with fixtures`
Files (57):
```
config/seed_niches.yaml
scripts/make_fixtures.py
src/common/textsim.py
src/common/untrusted.py
src/research/__init__.py
src/research/comment_miner.py
src/research/common.py
src/research/niche_score.py
src/research/outlier_scan.py
src/research/velocity_radar.py
src/research/youtube_api.py
tests/fixtures/youtube/channels.json
tests/fixtures/youtube/comments.json
tests/fixtures/youtube/meta.json
tests/fixtures/youtube/search/ancient-civilization-explained.json
tests/fixtures/youtube/search/biology-explained-animation.json
tests/fixtures/youtube/search/chemistry-of-everyday-life.json
tests/fixtures/youtube/search/cognitive-bias-explained.json
tests/fixtures/youtube/search/compound-interest-explained.json
tests/fixtures/youtube/search/economics-explained-simply.json
tests/fixtures/youtube/search/empire-history-map-animation.json
tests/fixtures/youtube/search/energy-transition-explained-data.json
tests/fixtures/youtube/search/engineering-megaproject-explained.json
tests/fixtures/youtube/search/etymology-explained.json
tests/fixtures/youtube/search/geography-facts-maps.json
tests/fixtures/youtube/search/geopolitics-explained-map.json
tests/fixtures/youtube/search/global-trade-routes-explained.json
tests/fixtures/youtube/search/history-of-borders-map.json
tests/fixtures/youtube/search/history-of-technology-explained.json
tests/fixtures/youtube/search/how-ai-works-explained.json
tests/fixtures/youtube/search/how-airplanes-work-explained.json
tests/fixtures/youtube/search/how-ancient-people-built.json
tests/fixtures/youtube/search/how-big-is-space.json
tests/fixtures/youtube/search/how-computers-work-explained.json
tests/fixtures/youtube/search/how-the-human-body-works.json
tests/fixtures/youtube/search/how-the-internet-was-invented.json
tests/fixtures/youtube/search/how-was-it-built-engineering.json
tests/fixtures/youtube/search/map-projection-explained.json
tests/fixtures/youtube/search/math-explained-visually.json
tests/fixtures/youtube/search/math-puzzle-explained.json
tests/fixtures/youtube/search/origin-of-languages-map.json
tests/fixtures/youtube/search/physics-explained-simply.json
tests/fixtures/youtube/search/psychology-facts-explained.json
tests/fixtures/youtube/search/rise-and-fall-of-empires-explained.json
tests/fixtures/youtube/search/scale-of-the-universe-explained.json
tests/fixtures/youtube/search/shipping-chokepoints-explained.json
tests/fixtures/youtube/search/solar-power-explained.json
tests/fixtures/youtube/search/urban-planning-explained.json
tests/fixtures/youtube/search/why-american-cities-are-designed.json
tests/fixtures/youtube/search/why-borders-are-straight.json
tests/fixtures/youtube/search/why-does-it-happen-chemistry.json
tests/fixtures/youtube/search/why-does-physics-work.json
tests/fixtures/youtube/search/why-planes-fly-this-route.json
tests/fixtures/youtube/search/why-this-country-is-so-important-map.json
tests/fixtures/youtube/videos.json
tests/fixtures/youtube/videos_prev.json
tests/test_research.py
```
Message:
```
feat(research): add quota-capped YouTube research modules with fixtures

Read-only Data API client (quota tracked before calls, 24 h cache), outlier scan,
velocity radar, comment miner, niche scorer; own TF-IDF text similarity and
untrusted-text handling; 20 seed niches; synthetic fixtures.
```

---

## 7. `feat(publish): add distinctness guard, review queue, exports and owner-only upload`
Files (11):
```
src/compliance/__init__.py
src/compliance/distinctness.py
src/publish/__init__.py
src/publish/crosspost_export.py
src/publish/oauth.py
src/publish/timing.py
src/publish/youtube.py
src/review/__init__.py
src/review/queue.py
tests/test_publish.py
tests/test_step10.py
```
Message:
```
feat(publish): add distinctness guard, review queue, exports and owner-only upload

Near-duplicate guard (narration/title/structure), interactive human review queue,
TikTok/Reels export packages, jittered cadence-capped scheduling, private-by-default
resumable upload behind --live and approval gates, PKCE OAuth with out-of-repo tokens.
```

---

## 8. `feat(learning): add stats pull, bandit, retention forensics and weekly brief`
Files (12):
```
scripts/make_analytics_fixtures.py
src/analytics/__init__.py
src/analytics/pull_stats.py
src/analytics/retention_forensics.py
src/analytics/weekly_brief.py
src/learning/__init__.py
src/learning/bandit.py
src/learning/predictor.py
src/learning/topic_graph.py
tests/fixtures/analytics/videos.json
tests/test_cli_entrypoints.py
tests/test_learning.py
```
Message:
```
feat(learning): add stats pull, bandit, retention forensics and weekly brief

Beta-Bernoulli format bandit that can't learn clickbait, retention penalty table,
ridge predictor gated at 100 videos, topic graph, weekly operator brief.
```

---

## 9. `feat(quality): add automated animation, audio and writing checks`
Files (5):
```
src/quality/run.py
src/quality/visual.py
src/quality/writing.py
tests/test_gate_hardening.py
tests/test_quality.py
```
Message:
```
feat(quality): add automated animation, audio and writing checks

Safe zones, words on screen, WCAG contrast on real pixels, static-frame detection,
loudness/peak/silence checks, hook/readability/variety/banned-phrase checks.
```

---

## 10. `docs: add project memory, ADRs, security report and operations runbook`
Files (19):
```
CHANGELOG.md
CLAUDE.md
COMMITS.md
DECISIONS.md
LICENSES.md
PROGRESS.md
README.md
SECURITY.md
docs/ARCHITECTURE.md
docs/AUDIO.md
docs/ENGINE.md
docs/LEARNINGS.md
docs/OPERATIONS.md
security/REPORT.md
security/sbom.json
src/CLAUDE.md
```
Message:
```
docs: add project memory, ADRs, security report and operations runbook

README, progress/morning summary, decisions, licenses, security model and scan
report (SBOM), changelog, architecture/engine/audio/operations/learnings docs.
```
