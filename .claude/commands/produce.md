---
description: Produce an approved video (voice, spec, music, render, quality checks)
argument-hint: <video_id>
allowed-tools: Read, Grep, Glob, Bash(bash scripts/task.sh produce:*), Bash(bash scripts/task.sh quality:*), Bash(bash scripts/task.sh inspect:*), Bash(bash scripts/task.sh thumbnails:*), Bash(bash scripts/task.sh db:*)
---

Produce video `$ARGUMENTS`.

1. `bash scripts/task.sh db get $ARGUMENTS`: it must be in state `reviewed` with a human `approved_by`. If not, stop and report (the code also enforces this gate).
2. `bash scripts/task.sh produce --video-id $ARGUMENTS` (renders exactly the approved script recorded in the DB; refuses if it changed since approval)
3. `bash scripts/task.sh quality --video-id $ARGUMENTS --video output/videos/$ARGUMENTS/<file>.mp4 --spec output/videos/$ARGUMENTS/<file>.spec.json --script <script_path from db get> --audio output/videos/$ARGUMENTS/<file>.wav`. On PASS it marks the video `checked` itself. Read the report and look at `<file>_review/contact.png`.
4. If checks fail, report the failures for the owner/visual-director; the approved script can only change by going back through review (`request-changes`).
5. Render the 3 thumbnails (`bash scripts/task.sh thumbnails --specs ...`) and report the legibility results.
