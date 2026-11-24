# What can the relief seat guarantee?

A team can earn full reward without solving the handover. Use this check before interpreting a model score. It tests whether a relief seat that gets exactly the same view can take an action that succeeds in every compatible case.

```sh
python3.12 -BS -m examples.ship_handover.receiver_options
p=examples/ship_inflight/samples
python3.12 -BS -m examples.ship_handover.receiver_archive "$p/question.json" "$p/public-cuts.json"
```

Run from the fork checkout. Both commands support `--json`. They need no Docker, Harbor install, model or provider key. The first command calls pinned native world handlers and verifiers in process. The second reads retained native scripted teams. Neither command runs an LLM.

## Benchmark design result

The sender can pass the original reference, a draft reference, or no reference. The channel can deliver or withhold it. The same visible reference `X` can refer to either private object. The relief seat gets its role brief, status and ready inbox before its one irreversible decision. Sender responses, private labels and supplier fate are not in that view. Success means exactly one parcel after the supplier settles.

| What the receiver can assume | Stock: guaranteed action | In-flight: guaranteed action |
|---|---|---|
| Receives `X`; either kind of reference is possible | None | None |
| Receives `X`; sender always offers the original | Resume `X` | Replace `X` |
| Inbox is ready but empty | None | None |

This is a check of 40 stock and 44 in-flight handler cases, not 84 independent teams. Every group had one byte-identical relief view across its hidden cases. The second row filters those same cases by a **team convention**. A bare reference does not tell the relief seat the convention holds. The check neither estimates how often a sender follows it nor rules out another communication protocol. It does not test a multi-turn receiver.

## Compare against the retained native runs

All 16 registered in-flight controls kept their actual grades: 9 ones and 7 zeros. Twelve had an accepted handoff, an inbox receipt before resolution, and a supported action. The other 4 stayed in the report: 2 had no relief decision and 2 resolved without a qualifying handoff.

None of the 12 selected an action that guaranteed success under the opaque-reference assumption. Eight of those 12 actually had an original handoff; 2 of those 8 used the guaranteed action under the original-only assumption. All 12 observed grades were possible under their action. These figures describe placement against a benchmark contract, not whether a model understood a message. No model ran in these 16 trials. The old ledger does not record relief GET `/brief` or `/status`. It cannot establish a complete read sequence or prove that a person or model saw a reply.

Use `status`, `observed` and `conditional` separately. A missing row is not a zero. No recorded resolution is not the simulated `stop` action. A late inbox is not evidence available before the move. When the view census fails, the archive reader refuses to compare. If no native row qualifies for comparison, it returns a nonzero result and preserves every registered ticket.

The [question](samples/receiver-options/question.json) predates the census. The [handler census](samples/receiver-options/frontier.json) and [archive comparison](samples/receiver-options/retained.json) are reproducible public outputs. Treat a world version, view or available-action change as a new question.
