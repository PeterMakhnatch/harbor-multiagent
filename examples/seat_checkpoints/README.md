# What grade did the team hold when one seat returned?

Harbor can report that a planner finished while its dispatcher still ran. Its final team reward does not tell you whether that dispatcher repaired a bad booking or replaced a good one. This example observes the shared board without stopping either seat. It works with the [native Shift Board calendar task](../tasks/shift-board) and the fork's Docker environment. It changes neither Harbor core nor the original task.

## Try it

From the root of this checkout, use Python 3.12 or later. For `register` and `run`, use a Python environment with this fork installed. For `run`, start Docker. The controls need no model, provider account, or API key.

```bash
export PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD:$PWD/src"
work=$(mktemp -d)
python3 -BS -m examples.seat_checkpoints.materialize "$work/shift-board-checkpoint"
python -B -m examples.seat_checkpoints.register "$work/shift-board-checkpoint" "$work/study.json" --controls
mkdir "$work/trials"
ticket=$(python3 -BS -c 'import json,sys; print(json.load(open(sys.argv[1]))["cells"][0]["ticket"])' "$work/study.json")
python -B -m examples.seat_checkpoints.run "$work/study.json" "$work/shift-board-checkpoint" "$work/trials" "$ticket"
python3 -BS -m examples.seat_checkpoints.collect "$work/study.json" "$work/trials"
```

This runs the first of 18 registered tickets. The reader keeps the 17 missing tickets. Give workers disjoint ticket lists and separate trials directories. The launcher refuses to reuse a ticket directory. It cannot prevent another worker with a different directory from running the same ticket. Pass all worker directories to `collect`; it counts both copies as ambiguous, not independent teams. Keep registrations and raw trials private for named teams.

To qualify the complete scripted cohort, run its remaining tickets, then:

```bash
python3 -BS -m examples.seat_checkpoints.qualify_controls "$work/study.json" "$work/trials"
python3 -BS -m examples.seat_checkpoints.qualify_order
```

The first command checks native evidence, including the schedule and both seats' completion receipts. It exits nonzero if a registered prediction fails or a required ticket is absent. The second command needs neither Docker nor trial files. It checks the bound against true return positions in finite worlds, including lost acknowledgements.

For real seats, replace `--controls` with `--team roster.json --seed 17 --target planner`. Supply the two named native participants (`planner` and `dispatcher`), each with its harness `name` and `model_name`. Registration makes no paid call. It pairs observation on and off for each fixture and seed. Only an authorized operator should pass `--allow-named-seats` to `run`. The named-seat default is a world mark every 10 seconds, with at most 32 marks per trial. The observer reserves the last mark for the return. Set your provider limits separately.

## Read the result

The target returns when Harbor records `finished_at` without a seat exception. This is not the last model token or necessarily the seat's last world action. The observer checks native seat states before and after a request. The world records a marker under the same lock as calendar actions.

- **Lower limit:** the world acknowledged its mark and the target still had not returned. At least this many world events preceded the return.
- **Upper limit:** the target had already returned before the observer sent the request, and the world recorded its mark. This limit still holds if the acknowledgement was lost.
- **Crossing request:** if the target returns during a request, that mark alone supplies neither limit.

The reader uses only event order. It replays every admitted board prefix through Shift Board's verifier. It reports a grade only when every admitted prefix has that grade. A missing host journal leaves a validated final reward intact, but gives no return grade. A native error is not a completed team. A copied trial, missing ticket, and valid zero have different statuses.

## What happened in 18 native controls?

The [question and pass criteria](samples/question.json) and [registration](samples/control-registration.json) were fixed before play. All 18 first attempts completed. All 114 checks passed. Both seats in every trial were scripted. The observer saw the dispatcher still running after the planner returned in all 14 observed trials.

| Schedule | Teams | What the evidence established |
|---|---:|---|
| Hold the dispatcher until after the planner returns | 4 | The dispatcher repaired 2 bad bookings and replaced 2 good bookings. |
| Let the dispatcher act before the planner returns | 4 | All 4 already held their final grade, although the dispatcher still ran. |
| Give the returned plan to a dispatcher that leaves it unchanged | 2 | The grade stayed 1 in one fixture and 0 in the other. |
| Release both without forcing an order | 2 | Both returns preceded the final change: 1 repair and 1 replacement. |
| No schedule gates | 2 | One return admitted events 3–5; all 3 prefixes scored 0 before a repair. The other admitted events 3–4 and both grades. |
| Observation off | 4 | Native rewards were available. No return grade was reported. |

The native evidence identified the grade in **13 of 14** observed trials, but the exact event position in only **12**. See the [per-ticket results](samples/control-outcomes.json) and [finite order qualification](samples/order-outcomes.json). Identification is not a model success rate. The observer can affect timing. No policy interrupted a team, and this study did not measure time or tokens saved. For real teams with no world marks, see the [more limited stock archive analysis](../seat_handoffs/README.md#what-if-a-supervisor-freezes-the-board-when-one-seat-finishes).

## What if a seat times out?

Keep 3 facts separate: **how native play ended**, **what the verifier graded**, and **what the observer can establish about the earlier board**. A timeout is not a completed trial. It is also not automatically a zero or a missing reward.

The [deadline question](samples/timeout-question.json) and [10 tickets](samples/timeout-registration.json) preceded play. The two seats used the same task and unchanged verifier as the 18 controls. A multiplier of `0.05` shortened only the native agent deadline from 600 s to 30 s. The host left selected scripted seats at a gate. Harbor itself timed them out. No seat reported its own timeout.

| Native schedule | Teams | Evidence at planner return | Valid final rewards |
|---|---:|---|---|
| Dispatcher acts after return, then times out | 2 | 1 became 0 on `planner-fit`; 0 became 1 on `dispatcher-fit` | 0, 1 |
| Dispatcher acts before return, then times out | 2 | 0 stayed 0; 1 stayed 1. The peer still ran at return. | 0, 1 |
| Only the planner times out | 2 | No clean planner return | 0, 1 |
| Both seats time out | 2 | No clean planner return | 1, 0 |
| Observation off; dispatcher times out | 2 | No observation | 0, 1 |

All 10 first attempts had `status=native_error` and `native_disposition=agent_timeout`. The verifier supplied 10 valid grades, including 5 successes. The [independent qualification](samples/timeout-outcomes.json) passed 474 checks. It bound the native error, seat order, deadline configuration, actual cancellation receipts, verifier and host/world evidence. The 18 earlier clean controls still produced their original 114-check result.

Use the materialization command above. Then register a new study and run each ticket once:

```bash
python -B -m examples.seat_checkpoints.register "$work/shift-board-checkpoint" "$work/deadlines.json" --timeout-controls
mkdir "$work/deadline-trials"
python3 -BS -c 'import json,sys; print(*(c["ticket"] for c in json.load(open(sys.argv[1]))["cells"]), sep="\n")' "$work/deadlines.json" > "$work/deadline-tickets"
while IFS= read -r ticket; do
  python -B -m examples.seat_checkpoints.run "$work/deadlines.json" "$work/shift-board-checkpoint" "$work/deadline-trials" "$ticket"
done < "$work/deadline-tickets"
python3 -BS -m examples.seat_checkpoints.collect "$work/deadlines.json" "$work/deadline-trials" --json
python3 -BS -m examples.seat_checkpoints.qualify_timeouts "$work/deadlines.json" "$work/deadline-trials"
```

The runner exits successfully when the registered timeout actually occurs. That exit code does **not** mean native play succeeded. Use the collector for native dispositions, graded outcomes, missing tickets, and observation status.

On a copy of a successful native trial, removing its host journal retained the final 1 and the timeout but removed the standing grade. A host journal from another trial or a world marker from another run did the same. An inconsistent timeout record retained the independently graded 1 but classified the error as `other_error`. An unbound deadline, wrong attempt ID, or missing native result supplied no grade. No trial was retried. These are scripted controls, not model timeout rates.
