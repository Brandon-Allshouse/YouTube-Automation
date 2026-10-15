---
description: List approved, checked videos whose publish slot is due (publishing itself is owner-run)
allowed-tools: Read, Bash(bash scripts/task.sh db:*), Bash(bash scripts/task.sh timing:*)
---

1. List videos in state `checked` and `scheduled`: `bash scripts/task.sh db list --state checked` / `--state scheduled`.
2. For `checked` videos, propose a slot with `bash scripts/task.sh timing --video-id <id>` (jittered, cadence-capped) and show it. Recording it (`--schedule`) is owner-only and interactive.
3. Print the exact command the **owner** runs to upload (private by default):
   `bash scripts/task.sh publish --live --video-id <id>`
   Do **not** run it yourself. Uploading requires the owner's OAuth and is denied for agents in `.claude/settings.json`.
