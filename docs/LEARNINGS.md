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

## Research / YouTube API
- `search.list` `pageInfo.totalResults` is a coarse estimate that saturates (~1e6); never use it linearly as "supply".
- Shorts views routinely exceed channel subscribers, so a views/subs outlier rule is far noisier for Shorts; report formats separately.
- A full 20-niche pass (2 queries each) costs ≈ 4,000 units, almost all from `search.list` (100 units each); stats/comments are ~1 unit per call. Caching makes follow-up modules free.
- Tool-input JSON turns `​` into a literal zero-width character; build such regexes from `chr()` code points.
- `ruff check --fix` applies SIM905 (string `.split()` → giant list); disabled in pyproject.
- Host Python on Windows writes CRLF in text mode; bash scripts then fail inside Linux containers (`set: pipefail: invalid option`). Normalise `scripts/*.sh` to LF (`sed -i 's/\r$//'`) or write with `newline='\n'`. Consider a `.gitattributes` with `*.sh text eol=lf`.
- Bandit treats words after `# nosec BXXX` as test ids; keep the comment bare and put reasons elsewhere.
