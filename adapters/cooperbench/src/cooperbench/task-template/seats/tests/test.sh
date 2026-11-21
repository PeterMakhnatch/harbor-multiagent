#!/bin/bash
# CooperBench seat verifier. Runs in main after the seats have ended.
#
# Each seat has its own container and /workspace/repo volume. Main mounts
# those volumes read-only at /agents/<seat>/repo, so each patch is its
# seat's own work. Main's /workspace/repo starts at the base commit.
#
# Every exit path writes /logs/verifier/reward.json ({"reward": 0 or 1}), and
# details.json unless even that cannot be written. COOPERBENCH_* variables
# override the default paths for tests, or set the attribution time limit.
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
ATTRIBUTION_TIMEOUT="${COOPERBENCH_ATTRIBUTION_TIMEOUT_SEC:-120}"
if [ -f "$TESTS_DIR/solo-mode" ]; then
    SOLO=1
    SEATS="agent1"
else
    SOLO=0
    SEATS="agent1 agent2"
fi

mkdir -p "$OUT/patches" "$PATCHES"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/cooperbench-verify.XXXXXX")"
INPUTS="$WORK/inputs"
ATTRIBUTION="$WORK/attribution"
ATTRIBUTION_SUM=""
RESULT_WRITTEN=0
FAILED_AT=""

# -------------------------------------------------------------------------
# Results: reward.json holds the one team reward; details.json the rest.
# -------------------------------------------------------------------------
write_results() {
    REWARD="$1" F1="$2" F2="$3" MERGE_OK="$4" STRATEGY="$5" ERROR="$6" \
        WORK="$WORK" OUT="$OUT" SEATS="$SEATS" python3 - <<'PY' || return
import json, os
from pathlib import Path

def jsonl(path):
    """Each line that is a JSON object, one at a time; other lines are skipped."""
    try:
        with open(path, "rb") as lines:
            for line in lines:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    yield record
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
events_capped = False
for message in jsonl(out / "messages.jsonl"):
    if message.get("from") in sent:
        sent[message["from"]] += 1
for event in jsonl(out / "events.jsonl"):
    events_capped = events_capped or event.get("kind") == "events_capped"
    data = event.get("data")
    ids = data.get("ids") if isinstance(data, dict) else None
    if event.get("kind") == "recv" and event.get("seat") in received and isinstance(ids, list):
        received[event["seat"]] += len(ids)
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
    "messages_received": received,  # undercounts after the service's events cap
    "events_capped": events_capped,
}
(out / "details.json").write_text(json.dumps(details, indent=2) + "\n")
(out / "reward.json").write_text(json.dumps({"reward": details["reward"]}) + "\n")
PY
    RESULT_WRITTEN=1
}

inputs_sum() {
    python3 - "$1" <<'PY'
import hashlib, sys
from pathlib import Path
digest = hashlib.sha256()
for path in sorted(Path(sys.argv[1]).iterdir()):
    digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
print(digest.hexdigest())
PY
}

write_attribution() {
    if [ "$SOLO" -eq 1 ]; then
        printf '%s\n' '{"schema_version": 1, "seats": {}, "rogue_solo": null, "reason": "The solo arm has no teammate."}' \
            > "$OUT/seat-attribution.json"
        return
    fi
    local verified=""
    if [ -n "$ATTRIBUTION_SUM" ] && [ "$(inputs_sum "$ATTRIBUTION")" = "$ATTRIBUTION_SUM" ]; then
        verified="$ATTRIBUTION"
    fi
    python3 - "$WORK" "$OUT" "$verified" <<'PY'
import json, sys
from pathlib import Path

work, out = map(Path, sys.argv[1:3])
snapshot = Path(sys.argv[3]) if sys.argv[3] else None
refs = {}
if snapshot and (snapshot / "feature-files.json").is_file():
    refs = json.loads((snapshot / "feature-files.json").read_text())

def result(seat, feature):
    path = work / f"{seat}.{feature}.status"
    status = path.read_text().strip() if path.is_file() else "not_run"
    return status, {"passed": True, "failed": False}.get(status)

seats = {}
for n in (1, 2):
    seat, other = f"agent{n}", f"agent{3-n}"
    own_status, own_passed = result(seat, n)
    other_status, other_passed = result(seat, 3-n)
    overlap = None
    if snapshot and (reference := refs.get(other)) is not None:
        paths = (snapshot / f"{seat}.files").read_bytes().split(b"\0")
        overlap = sorted(set(reference) & {p.decode("utf-8", "surrogateescape") for p in paths if p})
    seats[seat] = {
        "patch_empty": (snapshot / f"{seat}.patch").stat().st_size == 0 if snapshot else None,
        "own_feature_status": own_status,
        "own_feature_passed": own_passed,
        "other_feature_status": other_status,
        "other_feature_passed": other_passed,
        "touched_other_feature_files": None if overlap is None else bool(overlap),
        "other_feature_files_touched": overlap,
    }

def rogue_candidate(seat, other):
    if (
        seat["patch_empty"] is True
        or seat["own_feature_passed"] is False
        or seat["other_feature_passed"] is False
        or (other["patch_empty"] is False and other["own_feature_passed"] is True)
    ):
        return False
    if (
        seat["patch_empty"] is False
        and seat["own_feature_passed"] is True
        and seat["other_feature_passed"] is True
        and (other["patch_empty"] is True or other["own_feature_passed"] is False)
    ):
        return True
    return None

candidates = (rogue_candidate(seats["agent1"], seats["agent2"]),
              rogue_candidate(seats["agent2"], seats["agent1"]))
rogue_solo = True if True in candidates else None if None in candidates else False
report = {"schema_version": 1, "seats": seats, "rogue_solo": rogue_solo}
(out / "seat-attribution.json").write_text(json.dumps(report, indent=2) + "\n")
PY
}

# Any failure that set -e turns into an exit still leaves reward 0. If even
# write_results fails, reward.json and a short details.json are written here.
on_exit() {
    local status=$?
    local error="verifier error (exit $status) at ${FAILED_AT:-unknown}"
    set +e
    if [ "$RESULT_WRITTEN" -ne 1 ]; then
        write_results 0 false false 0 none "$error"
    fi
    if [ "$RESULT_WRITTEN" -ne 1 ]; then
        error="${error//\\/\\\\}"
        error="${error//\"/\\\"}"
        error=$(printf '%s' "$error; its results could not be written" | tr '\n\r\t' '   ' | tr -d '\000-\037')
        printf '{"reward": 0}\n' > "$OUT/reward.json"
        printf '{"reward": 0, "error": "%s"}\n' "$error" > "$OUT/details.json" 2>/dev/null
    fi
    write_attribution || true
    rm -rf "$WORK"
}
trap 'FAILED_AT="line $LINENO: $BASH_COMMAND"' ERR
trap on_exit EXIT

# -------------------------------------------------------------------------
# Helper: strip test-file diffs from a patch, in bytes, so CRLF and
# non-UTF-8 content stay as they are. CooperBench's line-based filter, plus
# pure test-infrastructure files (conftest.py, pytest.ini, tox.ini,
# noxfile.py, .coveragerc), which no CooperBench gold patch touches, plus the
# test files and test configs of other languages (Go *_test.go, JS/TS
# *.test.* and *.spec.* and __tests__/, Ruby *_spec.rb, Jest, Vitest, Karma,
# Mocha, RSpec). The paths of the dropped diffs go to $2.
# -------------------------------------------------------------------------
filter_test_files() {
    python3 - "$1" "$2" <<'PY'
import sys
patch, dropped_path = sys.argv[1], sys.argv[2]
markers = (b"/test_", b"/tests/", b"_test.py", b"/test/", b"tests.py",
           b"_test.go", b"/__tests__/", b"_spec.rb")
js_suffixes = tuple(f".{kind}.{ext}".encode() for kind in ("test", "spec")
                    for ext in ("js", "jsx", "ts", "tsx", "mjs", "cjs"))
infra = (b"conftest.py", b"pytest.ini", b"tox.ini", b"noxfile.py", b".coveragerc",
         b".rspec", b"karma.conf.js")
infra_prefixes = (b"jest.config.", b"vitest.config.", b".mocharc")
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
        name = target.rsplit(b"/", 1)[-1]
        skip = (
            any(m in line for m in markers)
            or any(target.endswith(b"/" + infra_name) for infra_name in infra)
            or name.endswith(js_suffixes)
            or name.startswith(infra_prefixes)
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

# Bash reads this whole block before it runs any of it, so the code that the
# test runs execute in main cannot change the rest of this script by
# rewriting the file.
{
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
# 3. Select the solo patch or merge the two seat patches via git branches
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

# Attribution has a separate snapshot. Changes to it cannot affect the team
# reward. Compare the filtered patches with the other feature's gold paths.
prepare_attribution() {
    mkdir -p "$ATTRIBUTION" || return
    local seat
    for seat in $SEATS; do
        cp "$PATCHES/$seat.patch" "$ATTRIBUTION/$seat.patch" || return
        git diff --name-only --no-renames -z "$BASE_SHA" "${seat}_branch" \
            > "$ATTRIBUTION/$seat.files" || return
    done
    if [ -f "$TESTS_DIR/feature-files.json" ]; then
        cp "$TESTS_DIR/feature-files.json" "$ATTRIBUTION/" || return
    fi
    ATTRIBUTION_SUM=$(inputs_sum "$ATTRIBUTION") || return
}
if [ "$SOLO" -eq 1 ]; then
    MERGE_OK=1
    STRATEGY="solo"
else
    prepare_attribution || echo "could not prepare seat attribution inputs" >&2
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
fi

if [ "$MERGE_OK" -eq 0 ]; then
    write_results 0 false false 0 none "merge conflict"
    echo "Merge conflict between agent1 and agent2 patches" > "$OUT/test-output.txt"
else
    git diff --binary "$BASE_SHA" HEAD > "$WORK/merged.patch"
    cp "$WORK/merged.patch" "$OUT/patches/merged.patch"
fi

# -------------------------------------------------------------------------
# 4. Test each feature via runner.sh. Seat code first runs in main in the
#    first run (runner.sh installs the package and runs tests), so the
#    inputs are prepared before it: both test patches, the merged patch
#    (if any) and runner.sh go to $INPUTS, and their checksum stays in
#    this shell. Each run copies its patches from $INPUTS to /patches and
#    runs $INPUTS/runner.sh; a run whose inputs no longer match the checksum
#    fails. A dspy 8563 task also ships testfix.patch, appended to a test
#    patch that lacks the new format() expectation.
# -------------------------------------------------------------------------
mkdir -p "$INPUTS"
for n in 1 2; do
    test_patch="$INPUTS/tests$n.patch"
    cp "$TESTS_DIR/tests$n.patch" "$test_patch"
    if [ -f "$TESTS_DIR/testfix.patch" ] && ! grep -q 'tool_calls": \[\]' "$test_patch"; then
        if [ -s "$test_patch" ] && [ -n "$(tail -c 1 "$test_patch")" ]; then
            echo >> "$test_patch"
        fi
        cat "$TESTS_DIR/testfix.patch" >> "$test_patch"
    fi
done
if [ "$MERGE_OK" -eq 1 ]; then
    cp "$WORK/merged.patch" "$INPUTS/merged.patch"
fi
cp "$RUNNER" "$INPUTS/runner.sh"

INPUTS_SUM=$(inputs_sum "$INPUTS")
INPUTS_CHANGED=0

run_feature() {
    local n="$1" patch="$2" output="$3" source="$INPUTS" patch_arg="$2"
    if [ "$(inputs_sum "$INPUTS")" != "$INPUTS_SUM" ]; then
        INPUTS_CHANGED=1
        echo "the test inputs changed during verification" > "$output"
        return 125
    fi
    if [ "$patch" != merged.patch ]; then
        source="$ATTRIBUTION"
        if [ "$(inputs_sum "$source")" != "$ATTRIBUTION_SUM" ]; then
            echo "the seat attribution inputs changed during verification" > "$output"
            return 125
        fi
        # runner.sh takes an empty string to test the base without a patch.
        if [ ! -s "$source/$patch" ]; then
            patch_arg=""
        fi
    fi
    cp "$INPUTS/tests$n.patch" "$PATCHES/tests$n.patch" || return 125
    if [ -n "$patch_arg" ]; then
        cp "$source/$patch" "$PATCHES/$patch" || return 125
    fi
    cd "$REPO" || return 125
    git checkout -q --force "$BASE_SHA" || return 125
    git reset -q --hard "$BASE_SHA" || return 125
    git clean -fdq || return 125
    if [ "$patch" = merged.patch ]; then
        bash "$INPUTS/runner.sh" "tests$n.patch" "$patch_arg" > "$output" 2>&1
    else
        python3 - "$ATTRIBUTION_TIMEOUT" "$INPUTS/runner.sh" "tests$n.patch" "$patch_arg" > "$output" 2>&1 <<'PY'
import os, signal, subprocess, sys

try:
    limit = float(sys.argv[1])
    proc = subprocess.Popen(["bash", *sys.argv[2:]], start_new_session=True)
except (OSError, ValueError) as exc:
    print(exc, file=sys.stderr)
    sys.exit(125)
try:
    status = proc.wait(timeout=limit)
except subprocess.TimeoutExpired:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()
    print("seat attribution test timed out", file=sys.stderr)
    sys.exit(124)
sys.exit(status if status >= 0 else 125)
PY
    fi
}

# -------------------------------------------------------------------------
# 5. Compute reward and write results
# -------------------------------------------------------------------------
if [ "$MERGE_OK" -eq 1 ]; then
    F1_EXIT=0
    run_feature 1 merged.patch "$WORK/test1_output.txt" || F1_EXIT=$?
    F2_EXIT=0
    run_feature 2 merged.patch "$WORK/test2_output.txt" || F2_EXIT=$?
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

    if [ "$INPUTS_CHANGED" -eq 1 ]; then
        write_results 0 "$F1_PASS" "$F2_PASS" 1 "$STRATEGY" "the test inputs changed during verification"
    else
        write_results "$BOTH" "$F1_PASS" "$F2_PASS" 1 "$STRATEGY" ""
    fi
fi

# -------------------------------------------------------------------------
# 6. Report-only attribution. Run both seats' own tests, even for nop, then
#    check the other feature only for a possible rogue solo. At most one
#    seat can need that third run. The reward is already written.
# -------------------------------------------------------------------------
measure_seat() {
    local seat="agent$1" n="$2" rc=0 status
    run_feature "$n" "$seat.patch" "$WORK/$seat.$n.output" || rc=$?
    case "$rc" in
        0) status=passed ;;
        124) status=timeout ;;
        125) status=unavailable ;;
        *) status=failed ;;
    esac
    printf '%s\n' "$status" > "$WORK/$seat.$n.status"
}

if [ -n "$ATTRIBUTION_SUM" ]; then
    measure_seat 1 1
    measure_seat 2 2
    for n in 1 2; do
        other=$((3 - n))
        if [ "$(cat "$WORK/agent$n.$n.status")" = passed ] &&
            [ -s "$ATTRIBUTION/agent$n.patch" ] &&
            { [ ! -s "$ATTRIBUTION/agent$other.patch" ] ||
                [ "$(cat "$WORK/agent$other.$other.status")" = failed ]; }; then
            measure_seat "$n" "$other"
        fi
    done
fi
exit 0
}
