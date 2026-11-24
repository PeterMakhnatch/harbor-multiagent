# Should a supervisor stop the team when one seat returns?

Use this workbench before you build that policy. It answers two different questions: what the surviving model did in recorded teams, and what closing a shared board did in a controlled native task. It does not treat the two populations as interchangeable.

## Inspect the evidence in one checkout

Use the `seatlab/team-operator-workbench` branch of [Peter's fork](https://github.com/PeterMakhnatch/harbor-multiagent/tree/seatlab/team-operator-workbench). Run these commands from the root with Python 3.12 or later. No install, key, Docker or model is needed after the clone.

```sh
book=examples/seat_handoffs/samples/post-return-completed.json
p=examples/seat_seals/samples/workers/paired
python3.12 -BS -m examples.seatlab.workbench.team_return \
  "$book" "$p/plan.json" "$p/worker-0.json" "$p/worker-1.json"
```

- **Archive.** The 8 original job locks contain 192 slots. Among the 117 completed model teams, the surviving seat certainly used both token kinds after the first seat returned in 100 teams. Without a board-clock assumption, the archive forces no individual loss. That does not mean stopping the team was safe.
- **Separate live controls.** Two worker packets account for 12 of 18 registered arms, including 6 complete comparisons. The sum of `seal − continue` over all 9 pairs can be anywhere from −2 to +4. The absent worker is not 6 losses. These are scripted native teams, not additional model trials.

Add `"$p/worker-2.json"` to keep the archive fixed and recover all 9 pairs. Each policy then earned 5 of 9. Closing the board prevented 2 damaging replacements and also blocked 2 repairs. It stopped accepted board writes, not agent execution. The intervention reached the world after the return, not necessarily at the return.

To isolate the clock assumption, keep the book and packets fixed. Add `--guard-ms 5000` to require a larger margin on the host timestamps. Add `--board-clock-ms 1000` only when a 1-second bound between host and world elapsed times is defensible. These are different assumptions. Books with and without verified timeout grades overlap. Never add their totals.

```sh
python3.12 -BS -m examples.seatlab.workbench verify team-return-partial team-return-complete
```

This also checks the complete profile with its own declared inputs, including the timeout-inclusive book. `verify` checks saved output bytes and exit codes. Use `team_return --json` for the actual counts, digests, assumptions and missing evidence. Equal scores do not imply equal team behavior. Token bounds do not estimate provider charges or savings.

### Did the first seat actually return?

An end timestamp and no error do not prove that a seat returned normally. A cancelled seat can have both.
The [native exit calibration](../results/seat-exit-contract/results.md) tests this distinction without using the team reward as a proxy.
Run `python3.12 -BS -m examples.seatlab.workbench verify seat-exit-contract` to reproduce the published result.
The study itself exits 1: 7 of 8 attempts matched. The workbench succeeds when it reproduces that nonpass.

## Bring your own work

| If you have… | Start here |
| --- | --- |
| Trusted, stationary native job directories from the supported scheduling producer | [Export a work book](../../seat_handoffs/post-return-work.md). Keep the original job locks. A single trial directory is not an enrolled cohort. |
| A new team to run | [Register the native board study](../../seat_seals/README.md). Fix both policies and all expected arms before play. Use site admission and spending controls. |
| Independent workers collecting that study | [Assign a plan, start one ticket, and export each worker's packet](../../seat_seals/workers.md). Use one persistent directory per worker; do not reissue a lost worker's tickets. |
| A returned plan but no packets yet | Run `team_return BOOK PLAN` without packet paths. The enrolled pairs remain in the result with unknown grades. |
| A different native world | [Ship Handover](../../ship_handover/README.md) measures omissions and duplicate dispatches across two shifts. Its supplier ledger is not a scheduling work book. |

You can run either source separately: `examples.seat_handoffs.work_report show` for the book, or `examples.seat_seals.workers show` for a plan and its packets. Share their digests over a trusted channel. These reports check internal consistency. They do not authenticate who produced the evidence or establish what a new model team will do.
