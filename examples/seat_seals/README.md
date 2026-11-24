# When should a supervisor close a shared board?

A planner finishes and its dispatcher still runs. Should the supervisor prevent the dispatcher from changing the booking? This native two-seat Harbor task lets an operator test that policy without changing the seats, the calendar verifier, or the team reward.

The supervisor requests a decision after it observes a clean planner return. Both arms make that request. In `continue`, the world records it and accepts later actions. In `seal`, the world closes writes under the same lock that orders calendar actions. Reads still work. The dispatcher still runs. This is a board policy, not a cancellation or a token limit.

## Native result: 9 matched pairs

We froze the [question and pass criteria](samples/question.json) and then [enrolled 18 first attempts](samples/registration.json). All 18 ran in Harbor with two scripted seats and the stock Shift Board verifier. The independent reader passed 369 of 369 checks. Neither arm used a model. Infrastructure and model charges: $0.

| When the dispatcher acts | Matched pairs | Result of sealing instead of continuing |
|---|---:|---|
| After the world decision: planner has the correct booking | 2 | Prevented 2 harmful replacements. |
| After the world decision: dispatcher has the correct booking | 2 | Blocked 2 valid repairs. |
| Before the planner returns | 2 | Changed neither reward; the decision arrived after the write. |
| After the planner returns, before the world decision | 2 | Changed neither reward; the decision arrived after the write. |
| Dispatcher leaves the correct booking unchanged | 1 | Both teams earned 1. `continue` recorded a leave; `seal` recorded a refused leave. |

Each arm earned 5 of 9 possible rewards. That total hides four opposing effects. In the two return-to-decision pairs, all four passive return-grade intervals contained both 0 and 1. The scripted gate separately established that the dispatcher wrote after the native return. The decision receipt established that it wrote before the policy reached the board. A host timestamp does not provide that ordering by itself.

These are controlled timing examples, not estimates of how often a model repairs a booking. The intervention can change seat behavior. The study does not measure money saved, model quality, or how a cancelled seat would act. See the [per-arm outcomes](samples/native-outcomes.json).

## Run the study

Use Python 3.12 or later from the root of this fork. `register` and `run` need an environment with the fork installed. `run` also needs Docker. The other commands need only the checkout and Python. The controls need no provider key.

```bash
export PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD:$PWD/src"
work=$(mktemp -d)
python3 -BS -m examples.seat_seals.materialize "$work/shift-board-seal"
python -B -m examples.seat_seals.register "$work/shift-board-seal" "$work/study.json" --controls
mkdir "$work/trials"
python3 -BS -c 'import json,sys; print(*(r["ticket"] for r in json.load(open(sys.argv[1]))["cells"]), sep="\n")' "$work/study.json" > "$work/tickets"
while IFS= read -r ticket; do
  python -B -m examples.seat_seals.run "$work/study.json" "$work/shift-board-seal" "$work/trials" "$ticket" || printf 'Inspect first attempt: %s\n' "$ticket" >&2
done < "$work/tickets"
python3 -BS -m examples.seat_seals.collect "$work/study.json" "$work/trials"
python3 -BS -m examples.seat_seals.qualify "$work/study.json" "$work/trials"
```

`run` starts one ticket. It refuses to replace an occupied directory. Do not rerun failed or incomplete tickets. Multiple operators can receive disjoint tickets and keep separate trial roots. Give all roots to `collect`. A duplicate ticket in two roots is ambiguous. A registration alone is not a cluster lock.

For separate worker machines, use [the paired-worker operator](workers.md). It assigns tickets, refuses conflicting starts, and shows what can still be concluded before every worker returns.

For named seats, give `register` a private native roster with `--team roster.json --seeds 2` instead of `--controls`. It draws fresh seeds and matches both arms within each calendar fixture. Nothing calls a model during registration. An authorized operator must pass `--allow-named-seats` to `run`. Set provider limits separately. Use `collect --json`, not the scripted qualifier, for these natural schedules. A natural schedule has no stage gate. This study did not run the named-seat route.

## What each receipt proves

- **Final reward:** Harbor's native result, the task binding, the world cut and the unmodified verifier agree. A missing observer receipt does not turn a valid zero into an unknown reward.
- **Grade at return:** the host samples native seat states before and after each world request. The reader replays all compatible board prefixes. It reports an interval when the sampling order cannot identify the grade.
- **Policy applied:** the decision exists at an exact board prefix. The first request that started after a clean return must match it to support a return-linked intervention. A lost acknowledgement can still leave both witnesses. A world decision without a host journal does not prove why it happened.
- **Refused actions:** the bounded ledger counts observed refusals without private request contents. A truncated ledger is only a lower bound. It is not a log of actions a model would have taken in the other arm.
- **Gate:** a completed scripted gate needs the published ordered trace, not just the word `complete`. The gate is separate from the reward and the intervention.

We altered 13 separate copies of one native result. The collector kept the verified final in all 13. It refused false causal links and distinguished a lost acknowledgement from a lost journal. A second copy of a ticket made that ticket ambiguous. An empty archive still reported all 18 planned tickets. See the [receipt controls](samples/receipt-controls.json). These files are evidence from cooperating operators, not signed attestations.

For passive observation without a write policy, use [seat checkpoints](../seat_checkpoints/README.md). For post-return work measured on real model teams, see [the stock scheduling archive](../seat_handoffs/README.md).
