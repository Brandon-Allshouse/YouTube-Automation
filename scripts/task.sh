#!/usr/bin/env bash
# Single entry point for every common command; the Makefile delegates here so hosts without
# `make` (e.g. Windows + Git Bash) use the same targets: `bash scripts/task.sh <target>`.
# Everything runs inside hardened containers (docker-compose.yml); nothing installs on the host.
set -euo pipefail
export MSYS_NO_PATHCONV=1  # stop Git Bash rewriting /app paths into C:/Program Files/Git/app

cd "$(dirname "$0")/.."
mkdir -p data output logs models

dc() { docker compose run --rm "$@"; }

# Test targets accept pytest options and paths under tests/ only, so nothing written elsewhere
# (e.g. by an agent under data/) can be executed as a test.
check_test_args() {
  for arg in "$@"; do
    case "$arg" in
      -*) ;;
      tests/*) case "$arg" in *..*) echo "refused test path: $arg" >&2; exit 2 ;; esac ;;
      *) echo "refused test argument: $arg (only options and paths under tests/)" >&2; exit 2 ;;
    esac
  done
}

target="${1:-help}"
shift || true

case "$target" in
  build)        docker compose build ;;
  lock)         dc lock sh -c "pip-compile -q --generate-hashes --allow-unsafe --strip-extras -o requirements.lock requirements.in && pip-compile -q --generate-hashes --allow-unsafe --strip-extras -o requirements-dev.lock requirements-dev.in" ;;
  fetch-assets) dc fetch python -m scripts.fetch_assets "$@" ;;
  shell)        dc dev bash ;;
  test)         check_test_args "$@"; dc dev python -m pytest -m "not slow" "$@" ;;
  test-all)     check_test_args "$@"; dc dev python -m pytest "$@" ;;
  coverage)     check_test_args "$@"; dc dev python -m pytest --cov --cov-report=term "$@" ;;
  lint)         dc quality sh -c "ruff check . && ruff format --check ." ;;
  format)       dc lock sh -c "ruff format . && ruff check --fix ." ;;
  typecheck)    dc quality mypy ;;
  quality)      dc quality python -m src.quality.run "$@" ;;
  security)     dc security bash scripts/security_scan.sh ;;
  render-demo)  dc render python -m src.production.produce --demo "$@" ;;
  demo)         dc render python -m src.production.produce --demo "$@" ;;
  research)     # research <module> [args]: only allowlisted, read-only research modules
                mod="${1:-outlier_scan}"; shift || true
                case "$mod" in
                  outlier_scan|niche_score|velocity_radar|comment_miner) dc research python -m "src.research.$mod" "$@" ;;
                  *) echo "unknown research module: $mod" >&2; exit 2 ;;
                esac ;;
  analytics)    # analytics <module> [args]: offline analysis on stored stats
                mod="${1:-weekly_brief}"; shift || true
                case "$mod" in
                  pull_stats|retention_forensics|weekly_brief) dc dev python -m "src.analytics.$mod" "$@" ;;
                  *) echo "unknown analytics module: $mod" >&2; exit 2 ;;
                esac ;;
  learn)        dc dev python -m src.learning.bandit "$@" ;;
  dashboard)    dc dev python -m src.dashboard.build "$@" ;;
  produce)      dc render python -m src.production.produce "$@" ;;
  timing)       dc dev python -m src.publish.timing "$@" ;;
  export)       dc render python -m src.publish.crosspost_export "$@" ;;
  publish)      # OWNER ONLY (denied for agents in .claude/settings.json). Uploads default to private.
                dc publish python -m src.publish.youtube "$@" ;;
  oauth)        # OWNER ONLY: one-time consent per channel, e.g. oauth authorize --channel atlas
                dc publish python -m src.publish.oauth "$@" ;;
  thumbnails)   dc render python -m src.production.thumbnail "$@" ;;
  validate-spec) dc dev python -m engine.schema.spec --validate "$@" ;;
  distinctness) dc dev python -m src.compliance.distinctness "$@" ;;
  review)       dc dev python -m src.review.queue "$@" ;;
  inspect)      dc render python -m src.quality.inspect "$@" ;;
  db)           dc dev python -m src.db "$@" ;;
  run)          dc render "$@" ;;
  help|*)
    cat <<'EOF'
Targets (bash scripts/task.sh <target> | make <target>):
  build         Build runtime + dev images
  lock          Regenerate hashed requirements*.lock
  fetch-assets  Download fonts, Natural Earth, Kokoro weights (checksum-verified)
  shell         Offline dev shell
  test          Fast tests (no renders)          test-all   All tests incl. slow renders
  coverage      Tests with coverage report
  lint          ruff check + format check        format     Apply ruff fixes/format
  typecheck     mypy --strict on engine/ and src/
  quality       Automated quality checks on rendered output
  security      Full security suite -> security/REPORT.md
  render-demo   Produce the demo Short to output/demo/
  research M   Research module M (outlier_scan|niche_score|velocity_radar|comment_miner); fixtures by default, --live for API
  analytics M   pull_stats|retention_forensics|weekly_brief      learn   bandit CLI
  produce ...   Production CLI (src.production.produce)          thumbnails ...  Thumbnail CLI
  validate-spec FILE   Validate a scene spec                     distinctness ...  Distinctness guard
  export ...    TikTok/Reels export packages for a Short
  timing ...    Upload-slot optimizer (jittered)                  publish ...  OWNER ONLY: YouTube upload (--live)
  review ...    Human review queue CLI                            inspect ...  Contact sheet / key frames
  db            SQLite CLI (python -m src.db ...)
  run <cmd>     Run an arbitrary command in the offline render container
EOF
    ;;
esac
