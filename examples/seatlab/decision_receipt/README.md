# Did the receiving seat cite the handoff when it decided?

`harbor/ship-decision-receipt` is a local, two-seat Harbor task. An outgoing desk worker can send a parcel reference. The relief worker makes the final dispatch decision. The task records whether that decision names the exact reference that the inbox returned. The parcel outcome remains the only reward.

## Prepare the task without running a team

From the fork root, use Python 3.12 or later:

```sh
work=$(mktemp -d)
work=$(cd "$work" && pwd -P)
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

## Prepare and check the four native controls

Use the fork's installed Python environment for the native configs. The commands below only prepare files and read evidence. They do not start Harbor, Docker, or a model.

```sh
py=.venv/bin/python
"$py" -B -m examples.seatlab.decision_receipt.control_inputs \
  "$work/task" "$work/controls" > "$work/control-plan.json"
pin=$(python3.12 -BS -c 'import json,sys; print(json.load(open(sys.argv[1]))["enrollment"])' "$work/control-plan.json")
"$py" -B -m examples.seatlab.decision_receipt.qualify \
  "$work/task" "$work/controls" --expected-enrollment "$pin"
```

Keep the enrollment digest outside the trial directory. It binds the exact task tree, native packager, relief adapter, relief client, four configs, four resolved native locks and the private output location. It does not pin every installed dependency or the container runtime. `control_inputs` refuses an existing output directory. The qualifier creates no trial directory. Before launch it reports `awaiting_evidence: 0/4 controls verified` (exit `3`).

For a separately authorized local calibration, use the same checkout. Start each config exactly once with `harbor trial start -c "$config"`. Use no override flags. Do not submit through a job, reuse a trial directory, or start a replacement after a crash. The outgoing and relief participants are scripted. The relief control reads the inbox once and sends one `close` decision. Only the `cite` flag changes between the two configs for each supplier fate. A missing confirmation does not cause a second decision.

Run the same qualifier after all four controls finish. Exit `0` requires four independent native trials and world runs. The saved decisions must match the calibration above. Exit `1` reports a contradiction, a seat failure, or incompatible evidence. Exit `2` means the enrollment or input cannot be checked. The output gives each recorded reward separately from its citation status. A valid control with reward `0` is not a failed calibration. A verified private record is not a signed receipt or a model result.

## Compare what existing benchmarks actually measured

```sh
python3.12 -BS -m examples.seatlab.decision_receipt.archive
python3.12 -BS -m examples.seatlab.workbench verify ship-decision-evidence
```

The report reads four released sets of scripted shipping controls. It distinguishes an empty inbox, a returned parcel reference and a citation at the decision. It reports physical parcel rewards separately for each release and supplier state. It never assumes a policy acted just because its name appeared in a roster.

In the 16 in-flight controls, the inbox returned before the decision 12 times. Four returns were empty. Only 8 decisions had a reference available to cite. The old world did not record whether any of those 8 cited it. Do not report this as 0 citations in 8 opportunities. The four released sets use different enrollment rules; do not add them as independent teams.

The [two-world release](../../ship_handover/two-worlds.md) distinguishes the empty symbol `none` from a null observation. The [disconnected release](../../ship_handover/disconnected-workers.md) reports 4 draft receipts before a decision. It does not show whether the 3 other accepted decisions followed an empty inbox return or no return. The report keeps those 3 inbox states unknown. A separate ticket has no recorded trial.

To inspect a saved cut from a future team, add `--receipt-cut "$trial/verifier/ship-decision-receipt-cut.json"`. Repeat the option for other cuts. The reader checks the world events and replays the parcel grade. It reports these cuts separately. A cut alone does not prove enrollment, role identity or whether both seats completed. Use `qualify` for the four registered calibration controls. Neither command starts a team.
