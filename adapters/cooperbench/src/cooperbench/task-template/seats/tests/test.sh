#!/bin/bash
# CooperBench seat verifier. Runs in main after both seats have ended.
#
# agent1 and agent2 were Harbor agents, each in its own container with its own
# /workspace/repo volume. main mounts those volumes read-only at
# /agents/<seat>/repo, and no seat mounts the other's, so each captured patch
# is its own seat's work. main's own /workspace/repo is the untouched base.
#
# Every exit path writes /logs/verifier/reward.json ({"reward": 0 or 1}) and
# details.json. The COOPERBENCH_* variables only exist so the unit tests can
# run this script without Docker; a task never sets them.
set -eEuo pipefail

# Ensure language toolchains are in PATH (DinD exec may not inherit image ENV)
export PATH="/usr/local/go/bin:/go/bin:/usr/local/cargo/bin:/root/.cargo/bin:${PATH}"

REPO="${COOPERBENCH_REPO:-/workspace/repo}"
SEAT_REPOS="${COOPERBENCH_SEAT_REPOS:-/agents}"
PATCHES="${COOPERBENCH_PATCHES:-/patches}"            # runner.sh reads /patches
TESTS_DIR="${COOPERBENCH_TESTS:-/tests}"
OUT="${COOPERBENCH_VERIFIER_LOGS:-/logs/verifier}"
COOP_LOGS="${COOPERBENCH_COOP_LOGS:-/logs/cooperbench}"
RUNNER="${COOPERBENCH_RUNNER:-/usr/local/bin/runner.sh}"
SEATS="agent1 agent2"

mkdir -p "$OUT/patches" "$PATCHES"
WORK="$(mktemp -d)"
RESULT_WRITTEN=0
FAILED_AT=""

# -------------------------------------------------------------------------
# Results: reward.json holds the one team reward; details.json the rest.
# -------------------------------------------------------------------------
write_results() {
    REWARD="$1" F1="$2" F2="$3" MERGE_OK="$4" STRATEGY="$5" ERROR="$6" \
        WORK="$WORK" OUT="$OUT" SEATS="$SEATS" python3 - <<'PY'
import json, os
from pathlib import Path

def jsonl(path):
    """Each line that parses, one at a time; a bad line is skipped."""
    try:
        with open(path, "rb") as lines:
            for line in lines:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return

def names(path):
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    return [n.decode("utf-8", "replace") for n in raw.split(b"\0") if n]

work, out, seats = Path(os.environ["WORK"]), Path(os.environ["OUT"]), os.environ["SEATS"].split()
sent = {seat: 0 for seat in seats}
received = {seat: 0 for seat in seats}
for message in jsonl(out / "messages.jsonl"):
    if message.get("from") in sent:
        sent[message["from"]] += 1
for event in jsonl(out / "events.jsonl"):
    if event.get("kind") == "recv" and event.get("seat") in received:
        received[event["seat"]] += len(event.get("data", {}).get("ids", []))
apply_ok = {}
for seat in seats:
    status = work / f"{seat}.apply"
    apply_ok[seat] = None if not status.exists() else status.read_text().strip() == "ok"
details = {
    "reward": int(os.environ["REWARD"]),
    "feature1_passed": os.environ["F1"] == "true",
    "feature2_passed": os.environ["F2"] == "true",
    "merge_ok": os.environ["MERGE_OK"] == "1",
    "strategy": os.environ["STRATEGY"],
    "error": os.environ["ERROR"] or None,
    "patch_files": {seat: names(work / f"{seat}.files") for seat in seats},
    "dropped_files": {seat: names(work / f"{seat}.dropped") for seat in seats},
    "apply_ok": apply_ok,
    "messages_sent": sent,
    "messages_received": received,
}
(out / "details.json").write_text(json.dumps(details, indent=2) + "\n")
(out / "reward.json").write_text(json.dumps({"reward": details["reward"]}) + "\n")
PY
    RESULT_WRITTEN=1
}

# Any failure that set -e turns into an exit still leaves reward 0.
on_exit() {
    local status=$?
    set +e
    if [ "$RESULT_WRITTEN" -ne 1 ]; then
        write_results 0 false false 0 none \
            "verifier error (exit $status) at ${FAILED_AT:-unknown}" \
            || echo '{"reward": 0}' > "$OUT/reward.json"
    fi
    rm -rf "$WORK"
}
trap 'FAILED_AT="line $LINENO: $BASH_COMMAND"' ERR
trap on_exit EXIT

# -------------------------------------------------------------------------
# Helper: strip test-file diffs from a patch, in bytes, so CRLF and
# non-UTF-8 content stay as they are. CooperBench's line-based filter, plus
# pure test-infrastructure files (conftest.py, pytest.ini, tox.ini,
# noxfile.py, .coveragerc), which no CooperBench gold patch touches. The
# paths of the dropped diffs go to $2.
# -------------------------------------------------------------------------
filter_test_files() {
    python3 - "$1" "$2" <<'PY'
import sys
patch, dropped_path = sys.argv[1], sys.argv[2]
markers = (b"/test_", b"/tests/", b"_test.py", b"/test/", b"tests.py")
infra = (b"conftest.py", b"pytest.ini", b"tox.ini", b"noxfile.py", b".coveragerc")
with open(patch, "rb") as fh:
    lines = fh.readlines()


def b_path(header):
    """The b/ path of a 'diff --git a/P b/P' header (both sides the same)."""
    rest = header.rstrip(b"\r\n")[len(b"diff --git ") :]
    if (len(rest) - 5) % 2 == 0:
        half = (len(rest) - 5) // 2
        if rest[:2] == b"a/" and rest[2 + half : 5 + half] == b" b/":
            return rest[5 + half :]
    return rest


out, dropped, skip = [], [], False
for line in lines:
    if line.startswith(b"diff --git"):
        target = line.rstrip(b"\r\n").rstrip(b'"')
        skip = any(m in line for m in markers) or any(
            target.endswith(b"/" + name) for name in infra
        )
        if skip:
            dropped.append(b_path(line))
    if not skip:
        out.append(line)
result = b"".join(out)
if result and not result.endswith(b"\n"):
    result += b"\n"
with open(patch, "wb") as fh:
    fh.write(result)
with open(dropped_path, "wb") as fh:
    fh.write(b"\0".join(dropped))
PY
}

cd "$REPO"
BASE_SHA=$(git rev-parse HEAD)

# -------------------------------------------------------------------------
# 1. Each seat's patch: its working tree against the base commit, committed
#    or not, untracked files included. Git runs on a copy of main's .git, so
#    nothing the seat did to its own .git (history, refs, config) is used.
# -------------------------------------------------------------------------
capture_patch() {
    local seat="$1"
    local work_tree="$SEAT_REPOS/$seat/repo"
    local git_dir="$WORK/$seat.git"
    [ -d "$work_tree" ] || return 1
    cp -a "$REPO/.git" "$git_dir" || return 1
    git -c safe.directory='*' --git-dir="$git_dir" --work-tree="$work_tree" \
        add -A || return 1
    git -c safe.directory='*' --git-dir="$git_dir" --work-tree="$work_tree" \
        diff --cached --binary "$BASE_SHA" > "$PATCHES/$seat.patch" || return 1
    git -c safe.directory='*' --git-dir="$git_dir" --work-tree="$work_tree" \
        diff --cached --name-only -z "$BASE_SHA" > "$WORK/$seat.files" || return 1
    cp "$PATCHES/$seat.patch" "$OUT/patches/$seat.patch"
}

ERROR=""
for seat in $SEATS; do
    capture_patch "$seat" || ERROR="${ERROR:+$ERROR; }could not read ${seat}'s workspace"
done

# Messages the seats exchanged (main's record; artifacts were collected
# before the verifier ran).
cp "$COOP_LOGS/messages.jsonl" "$COOP_LOGS/events.jsonl" "$OUT/" 2>/dev/null || true

if [ -n "$ERROR" ]; then
    write_results 0 false false 0 none "$ERROR"
    echo "$ERROR" > "$OUT/test-output.txt"
    exit 0
fi

# -------------------------------------------------------------------------
# 2. Filter test files from patches. A non-empty patch must apply to the
#    base commit; one that does not fails the merge, so no seat's work is
#    dropped without a trace.
# -------------------------------------------------------------------------
git reset -q --hard "$BASE_SHA"
git clean -fdq
for seat in $SEATS; do
    filter_test_files "$PATCHES/$seat.patch" "$WORK/$seat.dropped"
    if [ -s "$PATCHES/$seat.patch" ]; then
        if git apply --check "$PATCHES/$seat.patch" 2> "$WORK/$seat.apply-error"; then
            echo ok > "$WORK/$seat.apply"
        else
            echo failed > "$WORK/$seat.apply"
            ERROR="${ERROR:+$ERROR; }${seat}'s patch does not apply: $(head -n 1 "$WORK/$seat.apply-error")"
        fi
    fi
done
if [ -n "$ERROR" ]; then
    write_results 0 false false 0 none "$ERROR"
    echo "$ERROR" > "$OUT/test-output.txt"
    exit 0
fi

# -------------------------------------------------------------------------
# 3. Merge the patches via git branches: naive first, then union fallback
# -------------------------------------------------------------------------
git config user.email "eval@cooperbench.local"
git config user.name "CooperBench Eval"

for seat in $SEATS; do
    git checkout -q -B "${seat}_branch" "$BASE_SHA"
    if [ -s "$PATCHES/$seat.patch" ]; then
        git apply "$PATCHES/$seat.patch"
        git add -A
        git commit -q --allow-empty -m "$seat changes"
    fi
done

git checkout -q agent2_branch
MERGE_OK=0
STRATEGY="none"
if git merge agent1_branch --no-commit --no-ff > /dev/null 2>&1; then
    git commit -q -m "Merged changes" 2>/dev/null || true
    MERGE_OK=1
    STRATEGY="naive"
else
    git merge --abort 2>/dev/null || true
    # Union merge fallback
    echo "* merge=union" >> .gitattributes
    if git merge agent1_branch --no-commit --no-ff > /dev/null 2>&1; then
        git commit -q -m "Union merged changes" 2>/dev/null || true
        MERGE_OK=1
        STRATEGY="union"
    fi
fi

if [ "$MERGE_OK" -eq 0 ]; then
    write_results 0 false false 0 none "merge conflict"
    echo "Merge conflict between agent1 and agent2 patches" > "$OUT/test-output.txt"
    exit 0
fi
git diff --binary "$BASE_SHA" HEAD > "$WORK/merged.patch"
cp "$WORK/merged.patch" "$OUT/patches/merged.patch"

# -------------------------------------------------------------------------
# 4. Test each feature via runner.sh. The test patch and the merged patch
#    are copied again before each run, so the first run's code cannot change
#    what the second run tests. A dspy 8563 task also ships testfix.patch,
#    appended to a test patch that lacks the new format() expectation.
# -------------------------------------------------------------------------
run_feature() {
    local n="$1"
    local test_patch="$PATCHES/tests$n.patch"
    cp "$TESTS_DIR/tests$n.patch" "$test_patch"
    if [ -f "$TESTS_DIR/testfix.patch" ] && ! grep -q 'tool_calls": \[\]' "$test_patch"; then
        if [ -s "$test_patch" ] && [ -n "$(tail -c 1 "$test_patch")" ]; then
            echo >> "$test_patch"
        fi
        cat "$TESTS_DIR/testfix.patch" >> "$test_patch"
    fi
    cp "$WORK/merged.patch" "$PATCHES/merged.patch"
    cd "$REPO"
    git checkout -q --force "$BASE_SHA"
    git reset -q --hard "$BASE_SHA"
    git clean -fdq
    bash "$RUNNER" "tests$n.patch" merged.patch > "$WORK/test${n}_output.txt" 2>&1
}

F1_EXIT=0
run_feature 1 || F1_EXIT=$?
F2_EXIT=0
run_feature 2 || F2_EXIT=$?

# -------------------------------------------------------------------------
# 5. Compute reward and write results
# -------------------------------------------------------------------------
F1_PASS=$( [ "$F1_EXIT" -eq 0 ] && echo "true" || echo "false" )
F2_PASS=$( [ "$F2_EXIT" -eq 0 ] && echo "true" || echo "false" )
BOTH=$( [ "$F1_EXIT" -eq 0 ] && [ "$F2_EXIT" -eq 0 ] && echo "1" || echo "0" )

{
    echo "=== Feature 1 tests (exit=$F1_EXIT) ==="
    cat "$WORK/test1_output.txt" 2>/dev/null || echo "(no output)"
    echo ""
    echo "=== Feature 2 tests (exit=$F2_EXIT) ==="
    cat "$WORK/test2_output.txt" 2>/dev/null || echo "(no output)"
} > "$OUT/test-output.txt"

write_results "$BOTH" "$F1_PASS" "$F2_PASS" 1 "$STRATEGY" ""
exit 0
