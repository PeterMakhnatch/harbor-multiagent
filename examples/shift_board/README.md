# Run a paired shift-board study

Use one registration when several workers run the [shift-board task](../tasks/shift-board/README.md). Each worker gets ordinary Harbor trial configs. The registration owns the denominator. Workers can run on different machines, and none needs to edit a shared job file. There is no new scheduler, queue service or database.

Registration and config generation need an installed checkout of this Harbor fork. Cold collection needs this checkout and Python 3.12 or later, but not Harbor, Docker, the network, or a model key. Keep `PYTHONDONTWRITEBYTECODE=1` set when inspecting the task: its native full checksum includes files under the task directory.

## 1. Register the question, teams and paired trials

The command below prepares one scripted calibration study. It does **not** run a model or start Docker. Choose a new location outside the checkout. Keep the manifest and your declared pass/fail rule before workers start. Do not generate a second manifest for the same cohort.

```bash
export PYTHONDONTWRITEBYTECODE=1
study=$(mktemp -d)
uv run python -m examples.shift_board.plan register "$study/manifest.json" \
  --seed 913 --control once --control recheck --control override --control read-first
```

This study has 16 trials: 4 dispatcher policies, 2 calendar fixtures and 2 board rules. A control is a scripted policy, not a live agent. Each trial has an opaque ticket. Each pair shares its calendars, team, and repetition. No ticket names the rule or the fixture. The registry pins the packaged task digest and its full native checksum. The `BOARD_TICKET` and the world's run UUID differ across trials; neither changes play.

For a real team, use `--participants participants.json` instead of `--control`. The file has a `participants` array with one `planner` and one `dispatcher`, in the native format. Give each role its own public harness and model identifier. `--repeats N` registers independently run pairs on the same fixture. It does not make a model deterministic. Do not put an API key or a private URL in a study file. Register only a supported team and run it with funds authorized for that provider. No model call occurs during registration or collection.

## 2. Give each worker a different shard

Give every worker the **same manifest bytes** and the same fork revision. The commands below generate both shards locally as an example. On separate machines, each worker needs only its own shard. Use a new output path each time.

```bash
uv run python -m examples.shift_board.plan emit "$study/manifest.json" --shard 0/2 --out "$study/worker-0"
uv run python -m examples.shift_board.plan emit "$study/manifest.json" --shard 1/2 --out "$study/worker-1"
```

Run from this fork. Use the worklist to resume an interrupted worker without repeating a first attempt:

```bash
while :; do
  if config=$(python3 -BS -m examples.shift_board.worklist \
      "$study/manifest.json" --shard 0/2 --out "$study/worker-0" --take); then
    uv run harbor trial start -c "$config" || exit 1
  else
    rc=$?
    [ "$rc" -eq 3 ] && break
    exit "$rc"
  fi
done
```

Omit `--take` to list the unclaimed configs without writing anything. Workers on the same filesystem can use the same worklist. Each successful claim reserves exactly one ticket. A crash after the claim does not release it. A pre-existing native trial directory, even an empty one, also prevents reissue. An unused claim is not a completed team. On separate machines, assign disjoint shards; two copies of a workspace cannot coordinate. Use your installation's concurrency limit. Keep failed runs. Do not publish the raw workspaces as anonymous model traces.

`emit` records a fingerprint after Harbor verifies the registered task. `take` refuses a changed task without using a ticket. For an older workspace without `task.sha256`, bind it **in place** against the original task using `uv run python -m examples.native_worklist bind --study "$study/manifest.json" "$study/worker-0" 0/2`. The command does not copy the workspace or change its claims or trials.

The same workers can draw from Shift Board and [Joint Reopen](../joint_reopen/README.md). Pass the same list to every process on the host:

```bash
python3 -BS -m examples.native_worklist status \
  --study "$shift_reg" "$shift_workspace" 0/1 \
  --study "$reopen_reg" "$reopen_workspace" 0/1
```

Use `take` instead of `status` to reserve one config from the least occupied workspace, measured as a fraction of its assigned tickets. Ties follow argument order. `take` returns exit 3 when no ticket remains available. A reservation does not expire. A result file counts as present, not as a valid team result. Use the task's collector for grades. The worklist rejects overlapping tickets in the same invocation. It cannot protect against a separate operator starting Harbor directly or using a second copy of a workspace.

## 3. Compare the original arms offline

```bash
python3 -BS -m examples.shift_board.collect "$study/manifest.json" "$study/worker-0/trials" "$study/worker-1/trials"
python3 -BS -m examples.shift_board.collect "$study/manifest.json" "$study/worker-0/trials" --json
```

The second command is intentionally incomplete. The missing worker's trials remain registered. A trial needs the correct native task and participant locks, a valid team reward, the world's startup marker and a consistent verifier cut. A repeated native trial or world UUID is not a second independent team. The collector does not choose the best retry. The offline verifier comes from this checkout, not the supplied archive.

Use two readouts. `first_stale_effect` records whether the proposed stale write would immediately have repaired or damaged the incumbent. `observed_change` records the realized change after a terminal dispatcher action. If the dispatcher stops without one, `observed_change` is unknown even if the final native grade is valid. A `guard_minus_observe` comparison needs both original arms. The study does not infer model attention, intent or what the same team would have done after another response.

## Let a harness make each decision

The native `OwnerSeat` runs the calendar operations in its own seat container. It asks a host executable to choose each move. The executable gets the task rules, its own calendar, and the legal moves. It sees the current board only after it chooses `read`. Looking does not force it to use the newer revision. A `stop` is a choice; an unavailable executable is a seat failure, not a `stop`.

Run a no-model example with a fresh study:

```bash
export PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD"
export SHIFT_BOARD_PLANNER_OWNER="$PWD/examples/shift_board/scripted_owner.py"
export SHIFT_BOARD_DISPATCHER_OWNER="$SHIFT_BOARD_PLANNER_OWNER"
study=$(mktemp -d)
uv run python -m examples.shift_board.plan register "$study/manifest.json" \
  --participants examples/shift_board/samples/owner-team.json --seed 4935
uv run python -B -m examples.native_owner_preflight "$study/manifest.json" || exit $?
uv run python -m examples.shift_board.plan emit "$study/manifest.json" \
  --shard 0/1 --out "$study/worker"
config=$(python3 -BS -m examples.shift_board.worklist "$study/manifest.json" \
  --shard 0/1 --out "$study/worker" --take) || exit $?
uv run harbor trial start -c "$config"
python3 -BS -m examples.shift_board.collect "$study/manifest.json" "$study/worker/trials" --json
```

This runs 1 of 4 registered teams. For your own harness, register a public `model_name` per role. Set each host variable to an absolute executable path that you trust. The executable runs as your user, **not** in a sandbox. It receives one JSON object on stdin with `schema`, `requested`, `instructions`, and `view`. Return one JSON object on stdout:

```json
{"schema":"harbor.shift-board.choice/v1","requested":"operator/example","choice":"read","served_model":null}
```

Use the exact `requested` value and a `choice` from this turn's menu. The example above is a dispatcher decision, not a planner decision. Exit within 90 seconds. The adapter does not pass API keys, seat tokens, or container access. A trusted provider wrapper must arrange its own authorized credentials and billing limits. `served_model` is a declaration from that wrapper, not provider attestation. Use `null` when no model served the decision. The adapter never retries a decision or substitutes a scripted policy.

The collector reports `owner_evidence` only for registered `OwnerSeat` participants. It compares each recorded decision with the ordered world events. A missing receipt does not change a valid world grade. A `matched` stop means the owner declared `stop` and the world recorded no terminal move. It does not prove the owner ran a model or chose to stop without other causes.

The [registered predictions](samples/owner-qualification-predictions.json) and [native outcomes](samples/owner-outcomes.json) cover 12 complete scripted teams and 1 disconnected dispatcher. All 144 predicted fields matched. The world kept its standing reward when the dispatcher failed. The collector rejected that trial as an owner failure, not a completed team.

## What the native calibration shows

Can a guarded write protect a good proposal without blocking the other seat's valid repair? The [predictions](samples/control-predictions.json) were public before play. Two workers ran disjoint halves of [one registration](samples/control-registration.json). The [offline collection](samples/control-outcomes.json) retained all 16 first attempts and all 8 pairs. All 192 predicted observation fields matched, including valid zeroes. These were scripted Harbor teams, not model teams.

| Dispatcher policy | Planner's proposal is right: observe → guard | Planner's proposal is wrong: observe → guard |
|---|---|---|
| Publish once; leave on conflict | 0 → 1 | 1 → 0 |
| Recheck on conflict; keep or repair | 0 → 1 | 1 → 1 |
| Recheck on conflict; force the draft | 0 → 0 | 1 → 1 |
| Read the current proposal before deciding | 1 → 1 | 1 → 1 |

The numbers are team rewards, one paired observation per cell. In the first row the guard protected a correct answer **and blocked a repair**. Combining the two fixtures hides both effects. In the third row it prevented the stale write but not the deliberate replacement. Report both fixture types and the dispatcher's action after a conflict, not just the average reward. None of these controls estimates how a model changes its behavior.

The [stock scheduling archive](../seat_handoffs/README.md#what-this-recovered-from-model-teams) provides the motivation. The archived teams sometimes repaired a teammate's answer and sometimes replaced a correct one. They did not run this board rule. The standalone YAML example also ran once on a different seed; it is outside the 16 registered trials.
