# Security scan report

- **Date:** 2026-10-15
- **Command:** `bash scripts/task.sh security`, run inside the hardened `security` container (repo read-only, `.env` masked with `/dev/null`).
- **Overall status:** ✅ **0 (all checks pass)**
- Raw outputs sit next to this file (`*.txt`, `sbom.json`). They are regenerated on every run.

| Check | Tool | Result |
|---|---|---|
| Lint | ruff 0.17 (E, F, W, I, B, UP, S, N, SIM, RUF, PL, PT, C4) | ✅ All checks passed |
| Types | mypy 2.4 `--strict` on `engine/` + `src/` (87 files) | ✅ No issues |
| Python security linter | bandit 1.9.4 | ✅ 0 high / 0 medium / 0 low. 12 justified `# nosec` (listed below) |
| Known vulnerabilities (runtime) | pip-audit 2.10.1 on `requirements.lock` (hash-checked) | ✅ No known vulnerabilities |
| Known vulnerabilities (dev) | pip-audit on `requirements-dev.lock` | ✅ No known vulnerabilities |
| Secrets | detect-secrets 1.5 vs `.secrets.baseline` | ✅ No new secrets |
| Workflow schemas | check-jsonschema (`vendor.dependabot`, `vendor.github-workflows`) | ✅ Valid |
| Workflow lint | actionlint 1.7.12 | ✅ OK (shellcheck not installed, so `run:` scripts are not shell-linted) |
| Action pinning | grep for non-SHA `uses:` | ✅ All actions pinned to full 40-char SHAs |
| SBOM | cyclonedx-py, CycloneDX 1.6 | ✅ `security/sbom.json`, 21 runtime components |
| Tests + coverage | pytest 9.1 + coverage (incl. slow render tests) | ✅ 314 passed. Total 81 % |

## Coverage against the CLAUDE.md targets (≥ 80 %)
| Area | Coverage |
|---|---|
| `engine/core` (aggregate) | **91.4 %** (render.py, the FFmpeg orchestration, is the lowest file) |
| `src/compliance` + `src/learning` (aggregate) | **98.5 %** |
| Parsers/validators: `engine/schema/spec.py` 89 %, `src/production/script.py` 100 %, `src/common/config.py` 100 %, `paths.py` 93 %, `untrusted.py` 100 %, `channels.py` 97 %, `src/quality/legibility.py` 95 % | ✅ |
| `src/research/youtube_api.py` | 79 % (live HTTP paths are mocked; the real network path is untested by design) |

## Justified suppressions (`# nosec`)
| Where | Rule | Why |
|---|---|---|
| `src/common/proc.py` (import, `run`, `popen`) | B404/B603 | The single subprocess wrapper. Executables are allowlisted, argv is a list, `shell=False`, there is a timeout, and arguments are validated (tested). |
| `engine/core/render.py` import | B404 | Only `subprocess.PIPE` constants are used; processes start via `proc`. |
| `urlopen` in `youtube_api.py`, `oauth.py`, `pull_stats.py`, `youtube.py`, `fetch_assets.py` | B310 | Fixed `https://` endpoints (allowlisted hosts for downloads, re-checked after redirects). |
| `src/publish/oauth.py` `TOKEN_URL` | B105 | False positive: an endpoint URL, not a password. |
| `src/audio/tts.py` `DLOPEN_TMP` | B108 | A dedicated exec tmpfs used only while espeak-ng is dlopen'ed (ADR-017). |

## Secret-scanning scope
Excluded from detect-secrets as known non-secrets: generated scan reports (`security/*.txt|json`), lockfile hashes, and **synthetic** API fixtures (`tests/fixtures/youtube|analytics/`, random IDs). Pinned commit SHAs carry `# pragma: allowlist secret`. The real `.env` is gitignored, never committed (`git log --all -- .env` is empty), masked in whole-repo containers, and never logged (0 key-shaped strings in `logs/` and `data/research/` after the live research run).

## Container hardening (verified)
Non-root UID 1000, read-only root filesystem, `cap_drop: [ALL]`, `no-new-privileges`, CPU/memory/PID limits, no Docker socket, no privileged mode. Render/dev/quality have **no network**. `.env` reaches only the `research` and `publish` services, via `env_file`. The base image is pinned by multi-arch digest, and wheels are installed with `--require-hashes --only-binary=:all:`.

## Downloaded assets
17 files (fonts, Natural Earth, Kokoro weights) are verified against `security/checksums.txt`. These are trust-on-first-use entries recorded 2026-10-15 from pinned sources (google/fonts `b7c8331c`, natural-earth-vector `ca96624a`, kokoro-onnx release `model-files-v1.0`). Re-running `fetch-assets` fails on any mismatch.

## Independent review (read-only subagent)
Found 5 high, 4 medium and 4 low issues the scanners can't see: approval not bound to script/render content, agent code execution via test-argument pass-through and unrestricted Write/Edit in two slash commands, OAuth token paths readable by headless agents, and state-machine shortcuts. **All high and medium issues are fixed** with regression tests in `tests/test_gate_hardening.py` and `tests/test_publish.py` (ADR-030). Open (low): untrusted research JSON strings are cleaned but not individually wrapped; the CI `pip` upgrade is not hash-pinned; hidden `--db` test options accept any path.

## Open items for the owner
- **Rotate the YouTube Data API key** the build agent used, and restrict it to the YouTube Data API v3.
- CodeQL Action v3 is pinned (as CLAUDE.md asks); v4.38.3 exists, and Dependabot will propose it.
- Optional: install `shellcheck` in the dev image so actionlint lints `run:` scripts.
