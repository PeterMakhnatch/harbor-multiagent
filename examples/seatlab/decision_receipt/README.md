# Did the receiving seat cite the handoff when it decided?

`harbor/ship-decision-receipt` is a local, two-seat Harbor task. An outgoing desk worker can send a parcel reference. The relief worker makes the final dispatch decision. The task records whether that decision names the exact reference that the inbox returned. The parcel outcome remains the only reward.

## Prepare the task without running a team

From the fork root, use Python 3.12 or later:

```sh
work=$(mktemp -d)
python3.12 -BS -m examples.seatlab.decision_receipt.materialize \
  "$work/task" > "$work/package.json"
```

Keep `package.json`. Its `tree` covers every staged file, its name and its mode, including both seat instructions. A second invocation does not overwrite the task. Preparing the task starts no service and needs no provider key. Use the fork's native runtime when you later register a local task. If you publish the task, use a packager that [includes role instructions](https://github.com/PeterMakhnatch/harbor-multiagent/pull/134).

## Read a saved decision

```sh
python3.12 -IB "$work/task/tests/decision_verify.py" \
  "$trial/verifier/ship-decision-receipt-cut.json"
```

Pass a cut file. The verifier's no-argument entry point is for Harbor's running world, not offline inspection. A valid file yields JSON with `relief_action`, `reward`, `citation_status` and `cited_at_decision`. The last field is a symbol (`original` or `draft`), never a usable reference.

| `citation_status` | What the cut establishes |
| --- | --- |
| `no_decision` | No relief decision was accepted. A parcel could still settle. |
| `decided_without_returned_reference` | The world had not returned a reference before the accepted decision. |
| `returned_not_cited` | The world returned a reference before the decision. The decision did not cite it. |
| `cited_at_decision` | The relief seat included that same reference in its accepted decision. |

The last state records an action **at** the decision. It does not establish acknowledgement **before** the decision, comprehension, or a change of mind. A `resume` action that names a parcel for shipping does not count as a citation unless the seat also supplies `cite`.

## Why use a separate task?

The [16 saved controls](../../ship_inflight/samples/public-cuts.json) for `harbor/ship-inflight` did not record citations. In 8 cuts, the world returned a reference before relief decided. In 6, relief decided without one. In 2, relief did not decide. These were scripted teams. The new verifier rejects their schema; none count as a negative citation.

Before using a model, check a matched pair: send the original reference, return it to relief and have relief choose `close` both times. Only one decision should include `cite`. Check one pair with `SH_FATE=settles` and another with `SH_FATE=fails`. Citation should separate each pair. Reward should separate the two fates, **not** each pair. These are requirements for calibration, not observed trial results. See the [handoff author guide](../../seat_handoffs/author.md) for the earlier controls and what they can establish.
