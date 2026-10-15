---
description: Write the weekly operator brief (performance, bandit, kill/scale, next week's plan)
allowed-tools: Read, Grep, Glob, Write(./data/**), Bash(bash scripts/task.sh analytics:*), Bash(bash scripts/task.sh learn:*), Bash(bash scripts/task.sh db:*)
---

Use the **analyst** subagent to run `bash scripts/task.sh analytics weekly_brief`, then write
`data/briefs/weekly-<YYYY-WW>.md` for the owner: wins, losses, leading indicators vs thresholds in
`config/rules.yaml`, bandit allocations for next week, kill/scale recommendations (only on ≥ 10
videos and every 14 days), and a ≤ 10-minute action list for the owner.
