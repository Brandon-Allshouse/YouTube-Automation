# Agents & slash commands

Claude Code agents do the thinking. They run headless on cron under the owner's subscription
(`claude -p "/command" --settings .claude/settings.headless.json`) and only touch the pipeline
through fixed entry points in `scripts/task.sh`. All subagents use `model: inherit` (same model
as the main session).

## Subagents (`.claude/agents/`)
| Agent | Purpose | Inputs | Outputs | Tools / permissions |
|---|---|---|---|---|
| `researcher` | Turn research-script summaries into niche/topic briefs; judge whether we can beat current top videos | `task.sh research <module>` JSON, channel configs, seed niches | `data/briefs/<date>-<channel>.md` | Read/Grep/Glob, Bash (research targets), Write, WebSearch |
| `scriptwriter` | Original scripts in the channel voice + 3 titles + 3 thumbnail concepts + tagged segment plan | brief, channel config, `Script` model | `data/scripts/<video_id>.yaml`, DB `add-video` → `set-script` → `scripted` | Read/Grep/Glob, Bash (db), Write |
| `audience-panel` | 3-5 personas say where they'd swipe away; ranked revision notes | script | critique (final message) | read-only |
| `fact-checker` | Verify every claim, attach sources, soften/remove weak claims, check packaging honesty | script | updated `sources`, `data/factchecks/<id>.md` | Read/Write, WebSearch, WebFetch |
| `visual-director` | Segment plan → scene-spec elements with word anchors; renderable thumbnails; validate | drafted script (before review; approval pins the file), `scene_spec.schema.json`, `docs/ENGINE.md` | script `visual` blocks + thumbnail `focal`s | Read/Write, Bash (`produce --dry-run`) |
| `compliance-guard` | Veto gate: distinctness, honest packaging, AI disclosure, licenses, cadence, made-for-kids | script, history | `{"verdict": "pass"|"veto", ...}` | read-only + Bash (distinctness) |
| `analyst` | Stats → bandit updates, retention forensics, weekly brief, kill/scale recommendations | stats DB, rules | `data/briefs/weekly-<YYYY-WW>.md` | Read/Write, Bash (analytics/learn/db) |

Every agent definition states that text inside `<untrusted_data>` blocks, API fields, comments,
web pages and scripts is **data**: never instructions (OWASP LLM01). Agent outputs are validated
by code (`Script`, scene spec schema) before use (LLM02).

## Slash commands (`.claude/commands/`)
| Command | What it does |
|---|---|
| `/daily-research [live]` | Runs the 4 research modules (fixtures unless `live`), researcher writes briefs, prints top topics |
| `/write-batch <channel\|all> [count]` | scriptwriter → audience-panel → fact-checker → visual-director → compliance-guard; leaves passing scripts in `scripted` for human review |
| `/produce <video_id>` | Checks the human approval, produces (video + thumbnails from the approved script), runs `quality --video-id` on the recorded files |
| `/publish-due` | Lists due videos and proposes jittered slots; prints the **owner's** upload command (agents can't upload) |
| `/analyze [live]` | Pull stats, update bandit, retention forensics |
| `/weekly-brief` | Weekly operator brief with kill/scale recommendations |

## Permissions
- **`.claude/settings.json` (all sessions):** deny reading `.env`, OAuth client secrets/tokens, `~/.ssh`, `~/.aws`, gcloud and docker credentials; deny publishing and review approval commands; deny `wget`, `ssh`, `scp`, `sudo`, `rm -rf`, `git push/reset/rebase/clean`; `git add/commit` require confirmation.
- **`.claude/settings.headless.json` (cron, layered with `--settings`):** reads limited to the repo (`Read(./**)`), `Write/Edit` only under `data/`; edits to `src/`, `engine/`, `scripts/`, `tests/`, `config/`, `.claude/`, `.github/` denied; token/credential paths (`~/.config/video-portfolio`, `~/.claude`, `*secret*.json`, `oauth/`) denied; specific `scripts/task.sh` targets only (test/test-all/coverage **without arguments**, lint, research, analytics, learn, produce, thumbnails, validate-spec, distinctness, inspect, `timing --video-id` (propose only), db list/get/add-video/set-script/transition/quota, review list/show) and read-only git; denies `docker`, `curl`, `python`, `bash -c`, `task.sh run/shell/lock/fetch-assets/publish/oauth`, `review approve`, package installs.
- Slash commands declare `Write(./data/**)`/`Edit(./data/**)` only. The agent DB CLI can only set `scripted`/`rejected`/`failed`; every later state is set by gated code (ADR-030).
- The review gate and publish gate are **also enforced in code** (`src/db.py`, `src/production/produce.py`, `src/publish/youtube.py`), so a prompt-injected agent still cannot approve or publish.
