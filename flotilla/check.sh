#!/usr/bin/env bash
# Local checks for Flotilla, because the fork runs no CI: both test suites, lint, and a QA pass on a
# fresh build of every family and condition. Uses uv-managed Python 3.12 (never a system python3 for
# the tests). Builds go to a temporary folder that is removed at the end. Run from anywhere:
#     flotilla/check.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PY=3.12
echo "== flotilla tests"
uvx --quiet --python "$PY" --with pytest --with pyyaml pytest -q -c flotilla/tests/pytest.ini flotilla/tests
echo "== analysis tests"
(cd flotilla/analysis && uvx --quiet --python "$PY" --with pytest pytest -q tests)
echo "== lint"
uvx --quiet ruff check flotilla/families flotilla/games flotilla/world flotilla/build_tasks.py flotilla/make_jobs.py \
    flotilla/qa.py flotilla/analysis/flotilla_analysis flotilla/analysis/tests
echo "== build and QA"
tmp=$(mktemp -d "${TMPDIR:-/tmp}/flotilla-check.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
uvx --quiet --python "$PY" python flotilla/build_tasks.py --family commons --difficulty a --seats 3 --instances 0-1 \
    --out "$tmp" >/dev/null  # a game family (docs/COMMONS.md)
uvx --quiet --python "$PY" python flotilla/build_tasks.py --family split --difficulty a --seats 2 --instances 0-1 \
    --conditions silent,talk --out "$tmp" >/dev/null  # a game family (docs/SPLIT.md)
for family in schedule ledger puzzle; do
    uvx --quiet --python "$PY" python flotilla/build_tasks.py --family "$family" --instances 0-1 --out "$tmp" >/dev/null
done
uvx --quiet --python "$PY" python flotilla/build_tasks.py --family schedule --scripted --instances 0 \
    --conditions talk,talk-liar,talk-warned,talk-liar-warned --out "$tmp" >/dev/null
uvx --quiet --python "$PY" python flotilla/build_tasks.py --family probe --instances 0-1 --out "$tmp" \
    >/dev/null  # a behavior probe: 4 seats, the 4 arms (docs/PROBE.md)
uvx --quiet --python "$PY" python flotilla/build_tasks.py --family probe --variant verify --instances 0-1 \
    --out "$tmp" >/dev/null  # the probe's verify kind: 4 seats, its 3 arms (docs/PROBE.md)
uvx --quiet --python "$PY" --with pyyaml python flotilla/qa.py "$tmp/*/*"
echo "flotilla checks: OK"
