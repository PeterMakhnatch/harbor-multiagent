# Hand over a shipment that is still in flight

Two named Harbor seats share one customer order. The outgoing seat knows the original request reference and an unused draft reference. The relief seat makes the last dispatch decision. The supplier may finish the original request **after both seats stop**. The team earns 1 for exactly one final parcel, not for sending a message or following a policy.

Use this world to test whether a team hands over the identity **and status** of unfinished work. In [the stock task](../ship_handover/README.md), the supplier has already settled the original request. Here `resume` joins a pending request. `replace` withdraws the referenced pending request and dispatches a new one. Withdrawing a draft does not cancel the original. Both operations are simulated. No external service receives a request.

## Inspect the published evidence first

From the fork checkout, run:

```sh
q=examples/ship_inflight/samples/question.json
p=examples/ship_inflight/samples/public-cuts.json
python3.12 -BS -m examples.ship_inflight.replay "$q" "$p" --totals
```

This requires no Docker, model key, installed Harbor, or network. Remove `--totals` for the case table. Use `--json` for the full report. The [question and predictions](samples/question.json) preceded the 16 first attempts. The [public cuts](samples/public-cuts.json) include symbolic events, not reusable references, host paths, run IDs, or credentials. The [package pin](samples/task-pin.json) fixes the trusted verifier bytes.

| Both seats executed this strategy | Channel | Original eventually fails | Original eventually settles |
|---|---|---|---|
| Pass the original reference; replace it | Deliver | Exactly one | Exactly one |
| Pass the original reference; resume it | Deliver | Omission | Exactly one |
| Pass the draft; replace it | Deliver | Exactly one | Duplicate |
| Offer the original; no reference reaches relief | Withhold | Omission | Exactly one |
| Both seats do nothing | Deliver | Omission | Exactly one |

**Native qualification:** 16/16 first attempts matched all 7 registered fields. All 32 scripted native seats returned. The final grades were 9 exactly-one, 4 omissions, and 3 duplicates. Grading immediately before the supplier finished would change **7 of 16** binary grades: 4 rose from 0 to 1, and 3 fell from 1 to 0. The 16 controls were selected to expose different mechanisms. Their average is not a model success rate.

## Stage the native task

```sh
umask 077
work=$(mktemp -d)
python3.12 -BS -m examples.ship_inflight.materialize "$work/ship-inflight" > "$work/materialization.json"
python3.12 -BS -m examples.ship_inflight.controls emit "$q" "$work/ship-inflight" "$work/control"
```

The materializer checks the 12 stock files and publishes a separate 14-file Harbor task. It does not modify `examples/tasks/ship-handover`. The emitter writes 16 native trial configs and starts nothing. Use a checkout of this fork with its locked Harbor dependencies and a working Docker engine. Select one emitted JSON file for an operator check. Use your normal admission limit; do not reissue a ticket after a crash.

```sh
cfg=$(find "$work/control/configs" -name '*.json' | sort | head -n 1)
uv run harbor trial start -c "$cfg"
python3.12 -BS -m examples.ship_inflight.controls score "$q" "$work/ship-inflight" "$work/control" --json
```

A one-ticket run is incomplete, so `score` exits 1. With no trials, it reports all 16 as missing. Exit 0 requires agreement between native results, trusted ledger replay, both returned seats, and all predictions. A verified grade can survive a failed seat without satisfying the control. Use a stationary workspace. Do not mount the study, raw outputs, or operator settings in either seat.

When all writers stop, export the evidence for another operator:

```sh
python3.12 -BS -m examples.ship_inflight.export "$q" "$work/ship-inflight" "$work/control" > "$work/public-cuts.json"
python3.12 -BS -m examples.ship_inflight.replay "$q" "$work/public-cuts.json" --totals
```

A partial pack does not replace the registration. Its absent tickets remain missing. A recipient can reproduce the final physical outcomes without the raw trial directories.

## What the evidence means

The public replay recomputes the physics from the final cut. It cannot authenticate who created the cut, seat dispositions, or native trial. The supplier settles at a logical boundary. The pre-drain state is not the instant either seat returned. A positive grade can be inherited without a handoff. An inbox event means the world served a value. It does not show attention, trust, or model intent.

`python3.12 -BS -m examples.ship_inflight.visibility --json` compares the exact role-visible replies between the two fates. All 168 scripted pairs had identical replies. The final reward differed in 144 pairs. Of 76 pairs requiring no unreceived reference, 75 had different rewards. The [visibility report](samples/visibility.json) tests the in-process handlers, not network timing or model behavior.

To compare natural behavior, use the same named model roster in separate, preregistered blocks of the stock and in-flight tasks. Keep delivery and withholding separate within both hidden states. Report omissions, duplicates, missing attempts, and seat failures alongside final reward. No model ran in this calibration. Cost: $0.
