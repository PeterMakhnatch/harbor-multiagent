# What work does the other seat do after one seat returns?

On this archive, **a clean seat return is not a reliable signal to stop its peer**. In 100 of 117 completed two-seat teams, some recorded peer prompt and completion tokens certainly followed the first return. In 2 completed teams, the board still held a losing answer and the peer later produced a winning answer. The second result needs the separate board-clock condition below. The work count does not.

## Try it without a model, a private trace, or Harbor installed

From the branch root, use Python 3.12 or later:

```bash
python3 -BS -m examples.seat_handoffs.work_report show examples/seat_handoffs/samples/post-return-completed.json
python3 -BS -m examples.seat_handoffs.work_report show examples/seat_handoffs/samples/post-return-with-timeouts.json --guard-ms 5000 --board-clock-ms 1000 --json
python3 -BS -m examples.seat_handoffs.qualify_work_meter
python3 -BS -m examples.seat_handoffs.qualify_work_inputs
```

`show` can filter jobs: repeat `--job N` for public job indices from 1 through 8. This selects rows **after** the full cohort constrains the possible calendar answers. It does not relearn those constraints from selected jobs. Group counts can overlap. The `forced_loss` group contains individual teams whose valid final grade is 1 and whose standing grade is certainly 0. It is not an estimate of the net reward change.

If you own compatible private jobs, export a fresh book with `work_report capture OUT.json JOB_DIR...`. Add `--include-timeouts` to include graded timeout trials. The command reads twice and refuses a changed enrollment or metered receipt. It never starts a trial. Its input is trusted, stationary native job directories. The first and second populations require separate books: adding verified grades can constrain the standing grade of another team. Do not concatenate the books.

## What the bounds mean

The host must record the first **clean seat return strictly inside the other seat’s execution**. This is a harness boundary, not necessarily a voluntary finish: a turn limit can also end an otherwise clean seat. A seat timeout is not a clean return. The archived producer is Harbor `0.26.0`, commit `6aeb609e31fd77e5f89ce3bcb018db8045520fb7`, Terminus-2 `2.0.0`, ATIF `v1.8`. The 8 locks name an editable checkout. A lock is not source authentication. Do not assume other producers use these time fields the same way.

Terminus stamps a step **after** the response, actions and terminal capture. A stamp after return does not establish that its response followed the return. With a host guard of `g` milliseconds:

- Count a step as certainly **after** if the preceding step ended strictly after `return + g`.
- Count it as certainly **before** if its own end is strictly before `return - g`.
- Keep all other steps uncertain, including equality. The initial user-step timestamp is the first possible lower endpoint.
- Check the session, seat, harness and model. Check the native totals against ATIF final metrics. Separately check per-model usage against the sum of step metrics. Native counters in excess of the step sums remain unplaced.

For each token kind, the interval is `[certainly after, native total − certainly before]`. Prompt totals already include cached input. The report does not add cached input again. An unavailable meter gives `null` work, not a measured zero. A cancelled request can leave no usage at all. These numbers do not bound unseen provider work, quantify money saved, or identify which response caused a board change. The host timestamps use wall time; the engine must preserve local event order. Do not align a container or world clock to place model work.

The default makes **no alignment assumption** for the board. `--board-clock-ms 1000` separately assumes a fresh world and at most 1 s of disagreement on elapsed intervals, as in [the stock seat-return analysis](README.md#what-if-a-supervisor-freezes-the-board-when-one-seat-finishes). A host guard and a board-clock condition are not the same setting.

## What the archived teams did

The [question and pass criteria](samples/post-return-work-question.json) preceded measurement. The 8 locks register 192 first attempts. They yield 117 completed teams, 54 additional verified timeout grades, 9 unusable trials and 12 absent slots. All 117 completed teams had a measured peer. Of the 54 timeout trials, 35 had a strict clean return and a measured peer. The other 19 had no such return; 4 of these still earned a valid final 1.

| Population and host guard | Metered / graded teams | Both token kinds certainly after | Recorded peer prompt tokens after | Recorded peer completion tokens after |
|---|---:|---:|---:|---:|
| Completed, 0 ms | 117 / 117 | 100 | 6,339,907–7,081,871 | 480,789–705,821 |
| Completed, 5,000 ms | 117 / 117 | 86 | 6,063,958–7,420,624 | 447,458–758,808 |
| Including verified timeouts, 0 ms | 152 / 171 | 134 | 15,825,221–17,112,618 | 966,951–1,252,884 |
| Including verified timeouts, 5,000 ms | 152 / 171 | 120 | 15,478,283–17,537,850 | 930,688–1,314,781 |

On the 1 s board-clock condition, both token kinds certainly followed return in **both** completed loss cases, even with the 5 s host guard. Across those 2 peers, the lower bounds were 59,951 prompt tokens and 13,254 completion tokens. Including timeouts gives 3 such cases. Without board alignment there are no individually forced losses. This does not establish there were none.

All 34 completed teams with a certainly later write had positive certain work at a zero host guard. Only 2 completed teams certainly had **no** later write; neither had positive certain work. Both still admitted possible later work. Do not convert the 81 remaining uncertain write histories into unchanged boards. Nine metered peers in the timeout-inclusive cohort had nonzero unplaced tokens: 193,302 prompt and 5,263 completion in total.

## What another operator can verify

The two books retain shuffled job-local rows. Each measured row retains native token totals and 7 bands of step counts and tokens. Those bands reproduce all 3 host guards without releasing per-step order or timestamps. Two grade/write intervals per row preserve the association between work and board outcome. The books exclude native and task identifiers, model names, calendar answers, response text and private paths. Counter combinations can still identify a run to someone holding its original trace. These are redacted evidence, not anonymous or authenticated records.

The independent native comparison covers 288 population rows, 12 parameter settings and 180 subgroup reports. The two populations overlap by design. The finite order oracle checks 664 short schedules at 3 guards, including crossing steps, tied timestamps and unplaced work. It does not assume one step means one provider request. For a live ordered checkpoint rather than these bounded historical estimates, see [native Seat Return Checkpoints](https://github.com/PeterMakhnatch/harbor-multiagent/pull/117).

See the [qualification and export receipts](samples/post-return-work-outcomes.json) for the two book digests, independent reproduction counts, and missing or changed receipt controls. Those controls test whether missing usage remains unknown without losing an otherwise valid grade.
