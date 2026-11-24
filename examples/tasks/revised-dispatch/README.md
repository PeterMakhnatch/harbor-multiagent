# Revised dispatch

Can a booker revise its choice after a teammate receives new information? This is a two-seat Harbor task. The reporter sees a private slot and sends an initial report. The booker saves a draft. That action opens a second report, which can **confirm** the slot or **replace** it. The booker can commit at any point; the world does not force it to request or follow the revision. Only the final committed slot determines the native reward.

## Run the reference team

Use Docker and a checkout of this fork. From the repository root:

```bash
uv run harbor trial start -c examples/configs/features/revised-dispatch-oracle.yaml
```

Both participants use Harbor's scripted `oracle`. No provider key is required. The [example config](../../configs/features/revised-dispatch-oracle.yaml) sets the seed to `"39"` and the variant to `"replace"` under `environment.env`. These are world inputs, not seat inputs. Harbor records the literal values in `lock.json`. Do not use `${...}` references or shell exports for fixture selection.

To run a matched control, copy the config and change only `HANDOFF_VARIANT` to `"confirm"`. Keep `HANDOFF_SEED` the same; valid seeds range from 0 to 1000000. Each worker can use a separate config without changing the shell. To test one model seat, replace one `oracle` participant in a copy. Leave the other as an oracle. Never put the fixture or the verifier's decision file in a seat prompt.

After Harbor prints the trial name, inspect that trial directory:

```bash
python3 -m examples.seat_handoffs events trials/<trial-name> --boundary acknowledged
```

The revision's `timing` reports whether the booker acknowledged it before or after its irreversible commit. The verifier separately records whether the draft changed, the choice was correct, and the choice matched what the reporter actually sent.

## Exercise the measurement before spending tokens

In your copy of the config, set `env: {HANDOFF_POLICY: stale}` on the **booker** oracle participant. Choose a policy below. Do not set a policy on a model participant: the policy is a switch in the scripted solution, not part of the task.

With the default reporter, the revision always matches the world's final slot:

| Booker policy | Revision acknowledged | Choice on `replace` / `confirm` | What it checks |
|---|---|---|---|
| `fresh` (default) | Before commit | Correct / correct | A timely update can change the choice. |
| `stale` | No | Wrong / correct | A team can pass without requesting the revision. |
| `late` | After commit | Wrong / correct | A receipt after the decision is not timely. |
| `ignore` | Before commit | Wrong / correct | A timely receipt does not prove use. |
| `unconfirmed` | No, but the world prepared the reply before commit | Correct / correct | No acknowledgement does not imply no delivery. |

## Separate an accurate report from agreement

Set `env: {HANDOFF_REPORT_POLICY: alternate}` on the **reporter** oracle. Its initial report stays accurate. On the revision it sends a slot different from both its first and current briefs. This simulates an ordinary reporting error without changing the world truth, the booker's instructions or the reward. The default reporter policy is `as_briefed`.

In `verifier/decision.json` version 2, `revision_correct` says whether a sent revision named the correct slot. It is null if there was no revision. `commit_matches_acknowledged_revision` compares the booked slot to the **sent** slot. It is null unless the booker acknowledged the revision before committing. A late acknowledgement or a prepared reply is not enough. Version 1 did not record either field.

Try `confirm` with an `alternate` reporter. A `fresh` booker can match the report and book the wrong slot. An `ignore` booker can disagree with it and book the right slot. With an accurate reporter and an unchanged slot, both policies make the same choice. Agreement alone cannot tell you which policy a model used. The booker has no independent source of truth in this task.

The main container records event order, not wall-clock timestamps. The verifier freezes the world before it records closure. The [handoff reader](../../seat_handoffs/README.md) keeps closure separate from the native reward. The public artifacts contain symbolic phase names, IDs, event counts, and decision flags. The private scheduling state is not exported. Seat logs or trajectories can contain private instructions and receipt tokens; do not publish them as anonymized artifacts.

These scripted controls test the benchmark and the reader. They do not establish that a particular model waits, revises its choice, or benefits from an acknowledgement. For model comparisons, pair the same seeds on `confirm` and `replace`, include failed attempts, and report the two variants separately.
