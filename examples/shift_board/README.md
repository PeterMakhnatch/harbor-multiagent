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

Run the emitted configs from this fork. For example, on worker 0:

```bash
for config in "$study"/worker-0/configs/*.json; do
  uv run harbor trial start -c "$config" || exit 1
done
```

Use your installation's concurrency limit. Run each assigned config only once. Do not retry a failed first attempt into the same registered ticket. If a trial fails, keep its directory. Keep operator failures separate from the team's score. Workspaces contain native configs and logs; do not publish them as anonymous model traces.

## 3. Compare the original arms offline

```bash
python3 -BS -m examples.shift_board.collect "$study/manifest.json" "$study/worker-0/trials" "$study/worker-1/trials"
python3 -BS -m examples.shift_board.collect "$study/manifest.json" "$study/worker-0/trials" --json
```

The second command is intentionally incomplete. The missing worker's trials remain registered. A trial needs the correct native task and participant locks, a valid team reward, the world's startup marker and a consistent verifier cut. A repeated native trial or world UUID is not a second independent team. The collector does not choose the best retry. The offline verifier comes from this checkout, not the supplied archive.

Use two readouts. `first_stale_effect` records whether the proposed stale write would immediately have repaired or damaged the incumbent. `observed_change` records the realized change after a terminal dispatcher action. If the dispatcher stops without one, `observed_change` is unknown even if the final native grade is valid. A `guard_minus_observe` comparison needs both original arms. The study does not infer model attention, intent or what the same team would have done after another response.

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
