---
description: Draft, critique, fact-check and compliance-check scripts for one channel (or all)
argument-hint: <channel|all> [count]
allowed-tools: Read, Grep, Glob, Write(./data/**), Edit(./data/**), WebSearch, WebFetch, Bash(bash scripts/task.sh db:*), Bash(bash scripts/task.sh distinctness:*), Bash(bash scripts/task.sh produce --dry-run:*)
---

Write a batch of scripts for `$ARGUMENTS` (default: all channels, 1 Short each).

For each script:
1. **scriptwriter**: draft from the latest brief; register the video (`db add-video` → save `data/scripts/<video_id>.yaml` → `db set-script <video_id> data/scripts/<video_id>.yaml` → `db transition <video_id> scripted`).
2. **audience-panel**: critique; apply the top revision notes (scriptwriter revises).
3. **fact-checker**: verify every claim; attach sources; fix or remove weak claims.
4. **visual-director**: fill segment visuals and renderable thumbnail `focal`s; validate with `bash scripts/task.sh produce --dry-run --script data/scripts/<video_id>.yaml`. Re-run `db set-script` is not needed (same path).
5. **compliance-guard**: run checks. On veto: revise once, re-check; if still vetoed, `db transition <id> rejected` with the reasons.
6. Leave passing scripts in state `scripted` for the **human review queue**. Never mark anything reviewed or approved.

Finish with a table: id, channel, title, duration estimate, verdict, open questions for the owner.
