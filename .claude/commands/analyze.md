---
description: Pull stats, update the bandit, and run retention forensics on recent videos
allowed-tools: Read, Grep, Glob, Write(./data/**), Bash(bash scripts/task.sh analytics:*), Bash(bash scripts/task.sh learn:*), Bash(bash scripts/task.sh db:*)
---

Use the **analyst** subagent:
1. `bash scripts/task.sh analytics pull_stats` (fixtures unless OAuth is configured and $ARGUMENTS contains `live`).
2. `bash scripts/task.sh learn update`
3. For each video with ≥ 48 h of data: `bash scripts/task.sh analytics retention_forensics --video <id>`.
4. Report leading indicators per channel and the top 3 retention problems with proposed fixes.
