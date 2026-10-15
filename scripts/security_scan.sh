#!/usr/bin/env bash
# Full security suite, run inside the `security` container: bash scripts/task.sh security
# Writes machine output to security/*.txt|json; security/REPORT.md is the human summary.
set -uo pipefail
cd /app
out=security
mkdir -p "$out"
status=0
# generated scan reports, lockfile hashes and synthetic API fixtures (random IDs) are not secrets
DS_EXCLUDE='^(security/.*\.(txt|json)|requirements.*\.lock|\.secrets\.baseline|tests/fixtures/youtube/.*|tests/fixtures/analytics/.*)$'
step() { echo "== $1"; }

step "ruff";            ruff check . > "$out/ruff.txt" 2>&1 || status=1
step "mypy --strict";   MYPY_CACHE_DIR=/tmp/cache/mypy mypy > "$out/mypy.txt" 2>&1 || status=1
step "bandit";          bandit -c pyproject.toml -r engine src scripts -f txt -o "$out/bandit.txt" || true
                        bandit -c pyproject.toml -r engine src scripts -lll -q > /dev/null 2>&1 || status=1
step "pip-audit";       pip-audit --strict --require-hashes --disable-pip -r requirements.lock > "$out/pip-audit.txt" 2>&1 || status=1
                        pip-audit --strict --require-hashes --disable-pip -r requirements-dev.lock > "$out/pip-audit-dev.txt" 2>&1 || status=1
step "detect-secrets";  git ls-files -z -co --exclude-standard | grep -zvE "$DS_EXCLUDE" | xargs -0 detect-secrets-hook --baseline .secrets.baseline > "$out/detect-secrets.txt" 2>&1 || status=1
step "workflows";       { check-jsonschema --builtin-schema vendor.dependabot .github/dependabot.yml \
                          && check-jsonschema --builtin-schema vendor.github-workflows .github/workflows/*.yml \
                          && actionlint -color=false .github/workflows/*.yml && echo "actionlint: ok"; } > "$out/workflows.txt" 2>&1 || status=1
step "action pinning";  { grep -hnE '^\s*-?\s*uses:' .github/workflows/*.yml | grep -vE '@[0-9a-f]{40}( |$)' && echo "UNPINNED ACTIONS FOUND" || echo "all actions pinned to full SHAs"; } > "$out/pinning.txt" 2>&1
                        grep -q UNPINNED "$out/pinning.txt" && status=1
step "sbom";            cyclonedx-py requirements requirements.lock --of JSON -o "$out/sbom.json" > /dev/null 2>&1 || status=1
step "pytest+coverage"; COVERAGE_FILE=/tmp/cache/.coverage python -m pytest --cov --cov-report=term > "$out/pytest.txt" 2>&1 || status=1

echo "overall status: $status"
exit $status
