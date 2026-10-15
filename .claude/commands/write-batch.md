---
description: Draft, critique, fact-check and compliance-check scripts for one channel (or all)
argument-hint: <channel|all> [count]
allowed-tools: Read, Grep, Glob, Write(./data/**), Edit(./data/**), WebSearch, WebFetch, Bash(bash scripts/task.sh db:*), Bash(bash scripts/task.sh distinctness:*), Bash(bash scripts/task.sh produce --dry-run:*)
---

Write a batch of scripts for `$ARGUMENTS` (default: all channels, 1 Short each).

For each script:
1. **scriptwriter**: draft from the latest brief → `data/scripts/<id>.yaml`; register the video (`db add-video`, then `db transition <id> scripted`).
2. **audience-panel**: critique; apply the top revision notes (scriptwriter revises).
3. **fact-checker**: verify every claim; attach sources; fix or remove weak claims.
4. **visual-director**: fill segment visuals; validate with `bash scripts/task.sh produce --dry-run --script data/scripts/<id>.yaml`.
5. **compliance-guard**: run checks. On veto: revise once, re-check; if still vetoed, `db transition <id> rejected` with the reasons.
6. Leave passing scripts in state `scripted` for the **human review queue**. Never mark anything reviewed or approved.

Finish with a table: id, channel, title, duration estimate, verdict, open questions for the owner.
