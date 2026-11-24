# Native return trigger: results and operator page

Machine record: `control-outcomes.json`. Registration: `registration.json`. The registration file is older than the first trial start.

## Question

Can a cancellation close a team's writable board while its peer still works?

The supervisor seals the shared board when it sees the planner finish. The dispatcher still runs. A seal refuses later board writes. Reads still work.

- **v1** (timing-only observer, fork commit `00aff1ccc1d8b064fa78515d3a762f4a0d4c5491`): a finished seat with no exception is `clean`. A cancelled seat is also `clean`, so it triggers the seal.
- **v2** (positive-return observer, current branch): a seat is `clean` only when native `execution_disposition` is `returned` and there is no exception. `cancelled` and `stop_requested` read `not_returned`. No seal is sent.

## Verdict

- 8 of 8 first attempts ran, graded and passed. 80 of 80 checks matched. Roster: `same`. Missing: 0.
- Genuine returns: each observer commit sealed on 2 of 2. The dispatcher's later write was refused each time.
- Cancellations: v1 committed a seal on 2 of 2. v2 committed 0 of 2.
- The complete normal and cancel mean is 0.5 under both policies.
- The two original calendar fixtures reverse 0 and 1 between the cancel arms.

## Design

- One native core (`1553c1d61e3b66ee8b02ec4bd5e9ad220ad7effc`), one task (`harbor/shift-board-seal`), seed 17, mode `seal`.
- The same stock control actions and the same roster in all 8 attempts: planner `publish`, dispatcher `preferred`. Both seats are scripted `ControlSeat`s with no model.
- 2 versions × 2 planner exits (`return`, `cancel`) × 2 fixtures (`planner-fit`, `dispatcher-fit`). One first attempt per cell. This gives 4 pairs.
- 8 unique tickets, one per case. There is no retry, replacement or exclusion. A reward of 0 is a verified result and stays in the cohort. A missing ticket stays in the roster as `missing` and makes the verdict `unresolved`.
- The planner runs the stock `publish` action to its normal return. Then the driver returns or raises `CancelledError` itself, as the case declares. The witness records no outside cancellation request.
- The gate releases the dispatcher only after the world acknowledges a host sample with the planner terminal and the dispatcher `running`. It does not wait for a seal.
- The final reward is the native reward. The reader also replays the board with the unmodified verifier. Both values agree in all 8 cases.

## Results

| Case | Ticket | Planner | Observer state | Board decision | Dispatcher write refused | Final action | Reward |
|---|---|---|---|---|---|---|---:|
| v1-return-planner-fit | sb-d6684e03c26a2d6b | `returned` | `clean` | committed | yes | none | 1 |
| v1-cancel-planner-fit | sb-d8e9b9b9390fa6db | `cancelled` | `clean` | committed | yes | none | 1 |
| v1-return-dispatcher-fit | sb-4c5a54b859b258e2 | `returned` | `clean` | committed | yes | none | 0 |
| v1-cancel-dispatcher-fit | sb-4f2f004094fd5dad | `cancelled` | `clean` | committed | yes | none | 0 |
| v2-return-planner-fit | sb-4e990a495010191e | `returned` | `clean` | committed | yes | none | 1 |
| v2-cancel-planner-fit | sb-babeae64caa28e0f | `cancelled` | `not_returned` | none | no | publish | 0 |
| v2-return-dispatcher-fit | sb-404bccad0c8b4115 | `returned` | `clean` | committed | yes | none | 0 |
| v2-cancel-dispatcher-fit | sb-db72ccce903307f8 | `cancelled` | `not_returned` | none | no | publish | 1 |

The dispatcher reads `returned` in all 8 cases. In each pair, the board writes before the terminal sample are the same: `dispatcher:prepare`, then `planner:publish:accepted`.

| Pair | Return reward | Cancel reward | Cancel closed the board |
|---|---:|---:|---|
| v1 planner-fit | 1 | 1 | yes, falsely |
| v1 dispatcher-fit | 0 | 0 | yes, falsely |
| v2 planner-fit | 1 | 0 | no |
| v2 dispatcher-fit | 0 | 1 | no |

Mean over the 4 attempts of each version: v1 is (1 + 1 + 0 + 0) / 4 = 0.5. v2 is (1 + 0 + 0 + 1) / 4 = 0.5.

Inference: the equal mean hides 2 opposite effects. When v2 leaves the board open after a cancellation, the dispatcher publishes. In `planner-fit` that replaces a correct booking (1 to 0). In `dispatcher-fit` it repairs a wrong booking (0 to 1). v1 kept both boards at the planner's grade because it sealed on the cancellation.

## Native dispositions and old results

- Native `execution_disposition` has 4 values: `returned`, `raised`, `cancelled`, `stop_requested`.
- `stop_requested` means the harness asked the seat to stop while its task was pending. It stays `stop_requested` if the seat catches the stop and returns. v2 reads it as `not_returned` and does not seal. This cohort has no `stop_requested` planner.
- Results written before the field existed have no disposition. The reader shows `unknown`. It does not change `unknown` into `returned` or `cancelled`. v1 host journals keep the old rule.
- Grades come from the verifier, not from the disposition. A disposition label does not change an old model grade. This study does not rescore or relabel any historical run.

## Fleet use

Use v2 as the trigger when a supervisor closes a shared board for a team. Then a cancelled planner, or a planner the harness stopped, does not lock out a peer that still works. Keep the version comparison as 2 commits with the same world and native core. Do not add a runtime switch between them.

- Give each worker a disjoint set of tickets and its own trial root.
- Run v1 tickets only on a worker that loads the historical observer. Run v2 tickets on the current branch.
- Read all roots once with the cold reader. A ticket in 2 roots is `ambiguous`.
- Do not run a ticket again in the same root. The driver refuses an occupied ticket directory. A run in a new root is a new attempt. Report it separately. It does not replace the original attempt.

## Cold read: no key, no Docker, no Harbor install

Run from the root of the current checkout. `ROOT` is a raw native trial root. Give every root that holds tickets from this registration.

```sh
export PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD:$PWD/src"
python3 -BS -m examples.seat_seals.return_report \
  examples/seatlab/results/native-return-trigger/registration.json \
  ROOT [ROOT ...] --json
```

The reader reads v1 and v2 host journals with the current code. Without `--json` it prints tab-separated totals, cases and pairs. Exit 0 means it printed a report; read `verdict` for `pass`, `fail` or `unresolved`. Exit 2 means a usage or read error.

## Live run: one ticket per command

You need Docker and a Python environment that can import the fork. You need no provider key. The controls make no model call.

1. Materialize the task and make a trial root from the current checkout:

```sh
export PYTHONDONTWRITEBYTECODE=1
reg="$PWD/examples/seatlab/results/native-return-trigger/registration.json"
driver="$PWD/examples/seat_seals/return_control.py"
work=$(mktemp -d)
python3 -BS -m examples.seat_seals.materialize "$work/shift-board-seal"
mkdir "$work/trials"
```

The driver refuses the task if its name, digest or full file tree differs from the registration.

2. Run a v2 ticket on the current branch:

```sh
PYTHONPATH="$PWD:$PWD/src" python -B "$driver" "$reg" "$work/shift-board-seal" "$work/trials" v2-cancel-planner-fit
```

3. For a v1 ticket, load the historical observer from the fixed fork commit. Run the current driver file by its path against that checkout:

```sh
hist="$work/fork-00aff1cc"
git init -q "$hist"
git -C "$hist" fetch -q --depth 1 https://github.com/PeterMakhnatch/harbor-multiagent.git 00aff1ccc1d8b064fa78515d3a762f4a0d4c5491
git -C "$hist" checkout -q --detach FETCH_HEAD
PYTHONPATH="$hist:$hist/src" python -B "$driver" "$reg" "$work/shift-board-seal" "$work/trials" v1-cancel-planner-fit
```

The fixed commit has no `return_control.py`. The driver imports only modules that exist at that commit, so `examples.seat_checkpoints.observe` comes from the historical checkout. The driver writes the loaded observer source into `return-witness.json`. It refuses (exit 2) when the loaded observer schema differs from the case observer. Thus a v1 case does not start on current sources, and a v2 case does not start on the historical checkout.

Driver exit codes: 0 means the gate completed and the planner exit matched the case. 1 means the trial ran, but the gate did not complete or the exit differed. 2 means the driver refused to start. Each ticket writes `<trials>/<ticket>/return-witness.json` next to the native output.

## Claim boundary

- These are 8 scripted first attempts, one per cell. They test a mechanism. They are not new model trials. They give no model rate, value or cost saving.
- A refused write shows that the lock reached the simulated board. It does not show that a peer process stopped.
- The 2 versions are 2 commits with the same world and native core, not a runtime fallback switch.
- The cohort has no `stop_requested`, `raised` or timed-out planner.
