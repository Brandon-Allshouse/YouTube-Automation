---
description: Draft, critique, fact-check and compliance-check scripts for one channel (or all)
argument-hint: <channel|all> [count]
allowed-tools: Read, Grep, Glob, Write(./data/**), Edit(./data/**), WebSearch, WebFetch, Bash(bash scripts/task.sh db:*), Bash(bash scripts/task.sh distinctness:*), Bash(bash scripts/task.sh produce --dry-run:*), Bash(bash scripts/task.sh produce --script:*), Bash(bash scripts/task.sh quality --video:*), Bash(bash scripts/task.sh learn plan:*)
---

Write a batch of scripts for `$ARGUMENTS` (default: all channels, 1 Short each).

First get the formulas: `bash scripts/task.sh learn plan --channel <ch> --n <count>` (factored Thompson sampling over niche × hook × length × visual mix, research-informed priors). Each slot is one script's brief: write that niche, hook type, length and visual mix, and pick the topic inside the niche from the latest research (outliers, velocity, unanswered comment questions).

For each script:
1. **scriptwriter**: draft from the latest brief; register the video (`db add-video` → save `data/scripts/<video_id>.yaml` → `db set-script <video_id> data/scripts/<video_id>.yaml` → `db transition <video_id> scripted`).
2. **audience-panel**: critique; apply the top revision notes (scriptwriter revises).
3. **fact-checker**: verify every claim; attach sources; fix or remove weak claims.
4. **visual-director**: fill segment visuals and renderable thumbnail `focal`s; validate with `bash scripts/task.sh produce --dry-run --script data/scripts/<video_id>.yaml`. After any script edit, re-run `db set-script` (it refreshes the arm and the distinctness fingerprint).
5. **Preview + quality:** `bash scripts/task.sh produce --script data/scripts/<id>.yaml --out output/dev/<id>`, then `bash scripts/task.sh quality --video output/dev/<id>/<id>.mp4 --spec output/dev/<id>/<id>.spec.json --script data/scripts/<id>.yaml --audio output/dev/<id>/<id>.wav`. Read `output/dev/<id>/<id>_review/quality.md` and look at `contact.png`; fix the script (visuals, words on screen, contrast, hook) and re-render until every check passes. The owner watches this preview while reviewing (`review show` prints its path). This is not production: it stays in `output/dev/`.
6. **compliance-guard**: run checks. On veto: revise once, re-check; if still vetoed, `db transition <id> rejected` with the reasons.
7. Leave passing scripts in state `scripted` for the **human review queue**. Never mark anything reviewed or approved.

Finish with a table: id, channel, arm (`set-script` prints it), title, duration estimate, verdict, open questions for the owner.
