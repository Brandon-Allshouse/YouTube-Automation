# Learnings

Gotchas, failures and why, performance findings, platform quirks. Append as discovered.

## Containers / host
- **A whole-repo bind mount leaks `.env` into every container.** Services mount individual directories; whole-repo services mask `.env` with `- /dev/null:/app/.env:ro` (verified 0 bytes inside).
- **Git Bash rewrites `/app/...` arguments** into Windows paths. `scripts/task.sh` exports `MSYS_NO_PATHCONV=1`.
- `docker compose run` has no `--network` flag; network policy lives on the service definition.
- Adding `-v` to `compose run` does not override a service's read-only mount of the same target; use a service whose mount is writable (`lock`) for `ruff format`.
- pytest's cache provider fails on the read-only repo mount → `-p no:cacheprovider`; Hypothesis DB goes to `/tmp` via `tests/conftest.py`.
- `actionlint` in the container has no `shellcheck`, so shell-script checks inside `run:` blocks are skipped (workflow structure/expression checks still run).

## Dependencies
- skia-python 144 and onnxruntime 1.31 both publish cp311 manylinux **aarch64** wheels (checked on PyPI), so the image builds on Apple Silicon without the pycairo fallback.
- `phonemizer` and espeak-ng (bundled in `espeakng-loader`) are GPL-3.0+; fine for local use, matters only if the image is ever distributed (ADR-007).

## Audio
- Kokoro v1.0 ONNX gives no word timings; per-sentence synthesis + our alignment is required (ADR-016).
- phonemizer `dlopen`s a *copy* of libespeak-ng from a temp dir → fails on `noexec` tmpfs with "failed to map segment from shared object" (ADR-017).
- `scipy.signal.lfilter` is O(n × filter order): a comb with a 1600-sample delay is 1600 taps. Block-wise recursion (one numpy op per delay length) made music generation ~40× faster.
- Kokoro output tops out near 12 kHz (24 kHz sample rate); the air shelf adds sheen but can't create content above Nyquist.
- Music measured *after* ducking reads ~19-20 dB under the voice at -12 dB placement + 8 dB duck; good for narration-led content.

## Engine / components
- skia-python `addPoly` needs `skia.Point` objects (≈45 ms per 100k points), so map geometry is built once per projection state and camera moves are canvas transforms.
- skia-python `PathMeasure.getPosTan` returns `(pos, tan)` (no success bool).
- Shapes rotated across ±180° must have longitudes unwrapped, or both area math and drawing break (a horizontal streak across the map). Found by a Hypothesis property test.
- Word anchors with repeated words need `word#n` ("meant" appears twice in one sentence).
- The first demo render had 3 s of blank frames because an element waited for a word; components should appear on scene entry and use word anchors only for their *beats*.

## Voice / TTS
- What made Kokoro sound synthetic: blending two low-data voices (`bm_george` + `bm_fable`, graded C), speed 1.1, sentence-by-sentence synthesis (intonation resets at every full stop) and digital silence between sentences. Single voices picked by A/B at speed 1.0, one pass per segment and a room-tone floor fix most of it with no new dependency.
- kokoro-onnx 0.6.1 enables phoneme timings only if the model has an output named `duration`; the onnx-community timestamped export names it `durations`, so set `has_timings` yourself.
- espeak phonemizes differently in context: "from the" becomes one group (`fɹʌmðɪ`), so counting words against phoneme groups fails. Align by pronunciation (edit-distance DP) instead.
- Hugging Face LFS downloads redirect to regional CDN hosts (`us.aws.cdn.hf.co`). The fetcher allows `*.hf.co` because those files are checksum-pinned from the LFS record.
- Pitch statistics (F0 range and SD) did not separate natural-sounding from synthetic-sounding samples; the owner's ear is the judge.

## First real batch (2026-10-15)
- Narration rate depends on the voice: am_fenrir ≈ 2.5 words/s and af_heart ≈ 2.8 (speed 1.0, pauses included). Size scripts per channel.
- AnimatedMap `zoom` for a 9:16 frame: equal-earth at zoom ≈ 4-5 frames Eurasia from Kyiv to Korea; ≈ 9-10 frames a single country like Mongolia; mercator zoom 12-30 frames the Strait of Dover. Zoom 1-2 is a thin world band with empty space above and below: avoid it in Shorts.
- A map highlight filled with the accent color under a title in the accent color fails contrast; drop the highlight or the title.
- `region: full` on a DiagramBuild puts nodes under the captions and in the platform UI zones; use `center`.
- A StatCounter that enters long before its count word sits on "0" for seconds. Anchor its entrance (`at_word`) near the count, and cover the gap with a title.
- Timeline labels count toward the 7-words-on-screen limit; keep them to 1-2 words each.

## Learning loop
- An independent Beta posterior per full arm (niche × hook × length × visual) can't learn at a few videos a week. Learn each dimension separately (factored Thompson sampling) until about 50 videos justify a hierarchical model.
- If a label decides what the system learns, compute it in code where possible (length, visual mix). Only the hook type and niche are declared, and both are validated.
- A guard whose history is filled by a manual step never fires. Enforce it at the point where content enters the pipeline (`db set-script`).
- Min-max normalization of heavy-tailed evidence is fragile: one niche with 300× the gap of the others makes the rest look identical. Percentile ranks are robust. Low competition is only worth something where there is demand.

## Scoring
- A score relative to the channel's own median always averages about 50 per channel. It is good for "which of my videos work" but blind to whether the algorithm is growing the channel. Pair it with an absolute trend (reach growth per upload).
- Doubling views and likes together leaves engagement per view unchanged: "twice as good on everything" needs likes to grow 4×.
- Research "median views" comes from the top search results by views, which is survivorship-biased and useless as a 48 h benchmark.

## Pipeline integration
- Unit tests of each gate passed while the end-to-end path was broken in three places (agent never attached the script; headless permissions lacked `set-script`; visual-director told to edit approved scripts). Run the whole flow (`add-video → set-script → approve → produce --video-id → quality --video-id → timing --schedule → publish` dry run) on a backed-up DB after changing any stage.
- Anything uploaded must be derived from the database record, never from caller-supplied paths or script text (channel token, files checked by quality).
- YouTube custom thumbnails via the API need a verified channel and only matter for long-form; Shorts thumbnails are picked in the app.

## Research / YouTube API
- Any time-series measurement (velocity snapshots) must bypass the response cache: a 24 h cache makes a 12 h re-run silently record old view counts under a new timestamp.
- `search.list` `pageInfo.totalResults` is a coarse estimate that saturates (~1e6); never use it linearly as "supply".
- Shorts views routinely exceed channel subscribers, so a views/subs outlier rule is far noisier for Shorts; report formats separately.
- A full 20-niche pass (2 queries each) costs ≈ 4,000 units, almost all from `search.list` (100 units each); stats/comments are ~1 unit per call. Caching makes follow-up modules free.
- Tool-input JSON turns `​` into a literal zero-width character; build such regexes from `chr()` code points.
- `ruff check --fix` applies SIM905 (string `.split()` → giant list); disabled in pyproject.
- Host Python on Windows writes CRLF in text mode; bash scripts then fail inside Linux containers (`set: pipefail: invalid option`). Normalise `scripts/*.sh` to LF (`sed -i 's/\r$//'`) or write with `newline='\n'`. Consider a `.gitattributes` with `*.sh text eol=lf`.
- Bandit treats words after `# nosec BXXX` as test ids; keep the comment bare and put reasons elsewhere.
