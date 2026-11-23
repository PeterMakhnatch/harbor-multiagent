# Bring four independently owned programs to a native Harbor team

The [workbench](../../README.md#seatlab-workbench) replays results without running a team. The [timing study](gather-time.md) ran all four model turns through one personal client. This fork workflow lets four trusted owner processes answer through separate local sockets instead. Harbor still runs and grades the team. A socket exchange proves delivery and a reply, not which provider ran, how many model sessions it used or what it cost.

## Integration question and acceptance test, before any attempt

Can four independently managed programs on one machine supply the actual reporter and booker turns without giving their credentials to Harbor or giving an owner another seat's calendar? Run one team at a time. Keep every first attempt. Never classify these as subscription-study replicates.

1. On world 1208, run all three truthful reference reporters and a reference booker over external sockets in the serial arm. Require four bound exchanges, one submission and native reward 1. On 1209 in the parallel arm, make only the booker intentionally return a different valid slot. Require four bound exchanges, one submission and native reward 0.
2. On world 1210 in the parallel arm, start independent signed-in sessions for the booker and the first reporter. Use separate typed programs for the other two reporters. Do the same on 1211 in the serial arm. Require four bound exchanges, three exact reports and a bound native grade on both worlds. Report the actual grades even if either model is wrong. Confirm the two signed-in sessions separately against each owner's private receipt. The generic connector must report no model session or token count of its own.
3. Run one additional typed case with an owner deliberately returning no answer after a valid request. Require no manufactured reply or provider claim. Preserve the native result. Refuse missing, aliased or invalid listener inputs before a native output directory exists. Do not start or retry an owner on the operator's behalf.

All participants are local trusted programs that speak the existing one-turn SeatLab protocol. Paid keys, credit fallback and new Codex seats are excluded from these examples. This tests outside programs on the four-seat calendar protocol; it is not a reward comparison or a remote execution service.

## Operator guide: connect four owner programs

This section shows how to run one team after the registration above. It is not a distributed deployment. All four programs and Harbor run on one machine, as one user. Each program is managed separately and can be a different program.

### Before the first team

1. Use a checkout of this fork. Do the [native setup](peer-fanin.md): set `HARBOR_PYTHON` to the fork's isolated interpreter and `DOCKER_SLOT` to your admission wrapper, as described in the [two-seat study](shared-read.md).
2. Make sure that the checkout has the stock generator commit. The driver extracts the task generator with `git archive` from commit `71ddf40b14ea81dd8eb0c32663e11abfe21cd1e3`. A shallow clone does not have it. Run this command once in a shallow clone:

   ```sh
   git fetch --depth 1 origin 71ddf40b14ea81dd8eb0c32663e11abfe21cd1e3
   ```

   We ran this command once in a fresh shallow HTTPS clone. The driver then generated the real four-seat stock task for world 1208.
3. Choose a new result path `OUT` outside the repository. Never reuse a path.

### Seats and roles

Each socket belongs to one physical seat, `p1` to `p4`. The physical seat does not change. The booker role rotates by world: for world `W`, the booker is seat `p((W − 1200) mod 4 + 1)`. The booker asks the other three seats in cyclic order after its own seat. Each program learns its role only from the prompt it receives.

| World | Booker | Reporters, in request order |
|---|---|---|
| 1208 | p1 | p2, p3, p4 |
| 1209 | p2 | p3, p4, p1 |
| 1210 | p3 | p4, p1, p2 |
| 1211 | p4 | p1, p2, p3 |

The driver admits only the existing arm and world pairs: `all` and `stop` on 1200–1207, and `serial` and `parallel` on 1208–1215. The held-final recovery arms are excluded. Every team gets the tag `external`. This tag is not `live` and not a control. The published panels do not accept it, and the evidence reader for those panels does not accept it.

### The owner protocol

Every owner program uses the same one-turn protocol as the existing SeatLab seats. The program reads one line on standard input:

```json
{"schema":"seatlab.seat-turn/v1","prompt":"..."}
```

The last prompt line starts with `Input JSON: `. A reporter gets `{"own":[...]}`. The booker gets `{"own":[...],"reports":[...]}`. The program writes one line on standard output and exits 0:

```json
{"text":"{\"starts\":[3,17]}"}
```

A booker reply text is `{"slot":N}`. The text can have at most 8192 bytes. Do not add other fields. A reply has no model, session, token, cost or provider field. If the program sends no reply or exits with a nonzero status, no answer is sent for it. The relay gives the program 70 seconds.

[`fanin/example_owner.py`](fanin/example_owner.py) is a typed example owner. It checks the prompt with the existing SeatLab parser and writes no file. A reporter lists its own starts in every mode. `--mode` changes only what happens after a valid request:

- `correct`: the booker books the one start that is left after it intersects its own starts with the delivered reports. If more than one start or no start is left, it sends no reply.
- `wrong`: the booker books a different valid start ID.
- `decline`: the program sends no reply and exits with status 3.

The example does not know the world, the target or the answer. For the registered cases, give each seat a program as follows:

- Case 1: use `correct` on all four seats for world 1208, arm `serial`. For world 1209, arm `parallel`, use `wrong` on booker seat `p2` and `correct` on the other seats.
- Case 2: on world 1210, arm `parallel`, use your signed-in programs on seats `p3` (booker) and `p4` (first reporter). On world 1211, arm `serial`, use them on seats `p4` (booker) and `p1` (first reporter). Use the example owner in `correct` mode on the other seats.
- Case 3: choose and record the world, the arm and one seat before the run. Use `decline` on that seat.

### Trust and telemetry

The relay is not a sandbox. Every owner program runs as your user and is fully trusted. The relay gives the program only `HOME`, `PATH`, `TMPDIR` and `LANG`, and it discards the program's standard error. A signed-in program uses its own login and keeps its own receipts. Harbor gets no credential from it. The driver records only the exact request and reply bytes for each seat. It does not record which model or provider answered, the number of sessions, tokens or cost. The evidence row reports `model_sessions` and `tokens` as unknown. To confirm a signed-in session, compare the request with the program's private receipt yourself.

### Run one team

Each listener accepts one native turn and then exits. It waits at most 10 minutes for the turn. In the `stop` arm, a reporter that the booker does not need never gets a turn, so its listener keeps waiting. The script below stops every listener that it started when it exits, also after a failure or an interruption. It does not touch other processes.

The script waits until each listener says that it is ready. It does not connect to a socket, because a connection uses the seat's only turn. The driver also checks the sockets without connecting. The driver never starts, restarts or retries an owner program. It refuses a missing, aliased or invalid socket before it creates `OUT`.

To use your own program on a seat, replace the command after the seat name in that `serve` line. The program path must be absolute. Run the script with `sh` from the checkout root:

```sh
set -eu
: "${HARBOR_PYTHON:?}" "${DOCKER_SLOT:?}" "${OUT:?}"
WORLD=1208
ARM=serial
REPO=$(pwd -P)
PY=$(command -v python3.12)
RELAY=$REPO/examples/seatlab/visibility/local_seat.py
OWNER=$REPO/examples/seatlab/fanin/example_owner.py
ROOT=$(mktemp -d /tmp/sx-XXXXXX)
chmod 700 "$ROOT"
mkdir -m 700 "$ROOT/s" "$ROOT/log"
SOCK=$(cd "$ROOT/s" && pwd -P)
PIDS=

stop_owners() {
  for pid in $PIDS; do kill "$pid" 2>/dev/null || :; done
  for pid in $PIDS; do wait "$pid" 2>/dev/null || :; done
  rm -rf "$ROOT"
}
trap stop_owners EXIT
trap 'exit 130' HUP INT TERM

serve() {
  seat=$1
  shift
  "$PY" -IS "$RELAY" serve "$SOCK/$seat.sock" -- "$@" 2>"$ROOT/log/$seat" &
  PIDS="$PIDS $!"
}
serve p1 "$PY" -IS "$OWNER" --mode correct
serve p2 "$PY" -IS "$OWNER" --mode correct
serve p3 "$PY" -IS "$OWNER" --mode correct
serve p4 "$PY" -IS "$OWNER" --mode correct

tries=0
for seat in p1 p2 p3 p4; do
  until grep -qx 'local_seat: ready' "$ROOT/log/$seat" 2>/dev/null; do
    tries=$((tries + 1))
    if [ "$tries" -gt 20 ]; then
      echo "listener $seat is not ready:" >&2
      cat "$ROOT/log/$seat" >&2
      exit 1
    fi
    sleep 1
  done
done

export PYTHONPATH="$REPO:$REPO/src:$REPO/examples:$REPO/examples/seatlab"
status=0
"$DOCKER_SLOT" "$HARBOR_PYTHON" -B -m examples.seatlab.fanin.trial \
  --world "$WORLD" --arm "$ARM" --sockets "$SOCK" --out "$OUT" || status=$?
if [ -d "$OUT" ]; then
  python3.12 -BS -m examples.seatlab.fanin.external_evidence "$OUT" \
    --world "$WORLD" --arm "$ARM" || status=$?
fi
exit "$status"
```

The evidence reader binds each seat's request and reply to the native team and prints one redacted row. It keeps a missing reply as missing and keeps the native reward. A wrong but complete booking is a complete team with reward 0. Keep the row for every first attempt, also when the team is incomplete.
