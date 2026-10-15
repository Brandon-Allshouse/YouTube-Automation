# Operations

How the owner runs the portfolio day to day, how cron drives the agents, and how to go live.
All commands run from the repo root in Git Bash (Windows) or a normal shell (macOS/Linux).
`make <target>` works wherever `make` exists; it is identical to `bash scripts/task.sh <target>`.

## First-time setup
1. Install Docker Desktop and git (nothing else goes on the host).
2. `bash scripts/task.sh build`: builds the images (about 5 min the first time).
3. `bash scripts/task.sh fetch-assets`: fonts, Natural Earth, Kokoro weights (about 350 MB), verified against `security/checksums.txt`.
4. `bash scripts/task.sh test`, then `bash scripts/task.sh render-demo`. Check `output/demo/mercator-short.mp4`.

## Daily routine (about 10-15 minutes per video)
| When | What | How |
|---|---|---|
| Automatic (cron) | Research + drafting | `/daily-research` then `/write-batch all` (see cron below) |
| You, ~10 min | Review queued scripts | `bash scripts/task.sh review list`, then `bash scripts/task.sh review show <id>`, then `python -m src.review.queue approve <id> --as <your name>` (run interactively; it asks you to type the id back) or `request-changes <id> --note "..."` / `reject <id> --reason "..."` |
| Automatic | Produce approved videos | `/produce <id>`: `produce --video-id <id>` renders exactly the approved script; `quality --video-id <id> ...` marks it `checked` only if every check passes |
| You, ~2 min | Watch the render | Open `output/videos/<id>/*.mp4`; check `<id>_review/quality.md` and the thumbnails' `.phone.png` |
| You | Upload | `bash scripts/task.sh timing --video-id <id> --schedule`, then `bash scripts/task.sh publish --video-id <id>` (dry run), then `bash scripts/task.sh publish --video-id <id> --live` (uploads **private** with `publishAt`; YouTube publishes at the jittered slot) |
| You (optional) | TikTok / Reels | `bash scripts/task.sh export --video ... --spec ... --script ...`, then upload the files in `output/exports/<id>/` by hand |

## Weekly routine (about 10 minutes)
- Read `data/briefs/weekly-<YYYY>-W<WW>.md` (from `/weekly-brief`). Act on its action list.
- Every 14 days: kill/scale decisions per `config/rules.yaml` (the brief marks candidates; you decide).

## Cron (headless agents)
Headless runs use the strict permission layer:
```cron
# m  h  dom mon dow  command   (local time; cd into the repo first)
17 6  *   *   *    cd ~/YouTube-Automation && claude -p "/daily-research" --settings .claude/settings.headless.json >> logs/cron.log 2>&1
42 6  *   *   *    cd ~/YouTube-Automation && claude -p "/write-batch all" --settings .claude/settings.headless.json >> logs/cron.log 2>&1
23 */3 *  *   *    cd ~/YouTube-Automation && claude -p "/analyze" --settings .claude/settings.headless.json >> logs/cron.log 2>&1
5  8  *   *   1    cd ~/YouTube-Automation && claude -p "/weekly-brief" --settings .claude/settings.headless.json >> logs/cron.log 2>&1
```
On Windows, use Task Scheduler with the same commands in Git Bash. Production of approved videos can also be scheduled (`/produce` per approved id), but approval itself can never be automated: the review gate is enforced in code.

## Go-live checklist
1. **Rotate the YouTube Data API key** the build agent used (Cloud Console → Credentials → regenerate). Restrict it to *YouTube Data API v3*. Update `.env`.
2. Review the overnight work and commit it in the groups in `COMMITS.md`. Optionally run `pre-commit install`.
3. Push. Then in GitHub **Settings → Code security** enable: Dependabot alerts, Dependabot security updates, grouped security updates, and **Code scanning → CodeQL → Advanced** (so `.github/workflows/codeql.yml` is used; *default setup* would conflict). **CodeQL is free on public repositories; a private repository needs GitHub Code Security (paid), otherwise the CodeQL workflow fails.**
4. In Google Cloud: enable the **YouTube Analytics API**; create an **OAuth client (Desktop app)**; download its JSON **outside the repo** and set `YOUTUBE_OAUTH_CLIENT_SECRETS=/path/to/client_secret.json` in `.env`. Optionally set `VP_SECRETS_DIR` (default `~/.config/video-portfolio`).
5. Create the channel accounts (one per `config/channels/*.yaml`; rename the working titles). For each: `bash scripts/task.sh oauth authorize --channel <id>`. Open the printed URL, approve with that channel's Google account, and paste the final `http://127.0.0.1:8765/...` URL back. Tokens are stored outside the repo with mode 600.
6. Run research live: `bash scripts/task.sh research outlier_scan --live`, then `velocity_radar --live` (twice, ≥ 12 h apart, for acceleration), `comment_miner --live`, `niche_score --live`. Pick channels and niches.
7. Start the cron schedule. Keep cadence ≤ 1 Short/day and 2-4 long-form/week per channel (enforced by `timing`).
8. In YouTube Studio, verify each channel (needed for custom thumbnails) and set "not made for kids" defaults.

## Troubleshooting
| Symptom | Fix |
|---|---|
| `font missing` / `Kokoro weights missing` | `bash scripts/task.sh fetch-assets` |
| `failed to map segment from shared object` (TTS) | A container without the `/var/tmp/dlopen` exec tmpfs; use the compose services (ADR-017) |
| `set: pipefail: invalid option` | CRLF line endings in a `.sh` file; `sed -i 's/\r$//' scripts/*.sh` (`.gitattributes` forces LF) |
| Paths like `C:/Program Files/Git/app/...` | Git Bash path rewriting; use `scripts/task.sh` (sets `MSYS_NO_PATHCONV=1`) |
| `QuotaExceededError` | Daily cap reached (`config/rules.yaml: quota.session_cap_units`); wait for the UTC day to roll over |
| Review `approve` refused | It must run in an interactive terminal, with your name (not an agent name), typing the id back |
| Quality check fails | Read `<id>_review/quality.md`; fix the script visuals (visual-director) and re-run `/produce` |
| Render is slow | `RENDER_CPUS=<n>` in the environment; preview with `produce --script ... --scale 0.5` |
