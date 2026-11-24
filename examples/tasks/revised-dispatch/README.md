# Revised dispatch

Can a booker revise its choice after a teammate receives new information? This is a two-seat Harbor task. The reporter sees a private slot and sends an initial report. The booker saves a draft. That action opens a second report, which can **confirm** the slot or **replace** it. The booker can commit at any point; the world does not force it to request or follow the revision. Only the final committed slot determines the native reward.

## Run the reference team

Use Docker and a checkout of this fork. From the repository root:

```bash
HANDOFF_VARIANT=replace HANDOFF_SEED=39 uv run harbor trial start \
  -c examples/configs/features/revised-dispatch-oracle.yaml
```

Both participants use Harbor's scripted `oracle`. No provider key is required. Set `HANDOFF_VARIANT=confirm` with the same seed to keep the initial slot but remove the need to change it. Seeds range from 0 to 1000000. Record both settings with each run. To test a model, change a participant in a copy of the [config](../../configs/features/revised-dispatch-oracle.yaml). Leave the other participant as an oracle to test one seat at a time. Keep variants, seeds, and the verifier's decision file out of seat prompts.

After Harbor prints the trial name, inspect that trial directory:

```bash
python3 -m examples.seat_handoffs events trials/<trial-name> --boundary acknowledged
```

The revision's `timing` says whether the booker acknowledged it before or after its irreversible commit. `verifier/decision.json` separately says whether the draft changed and whether the final choice was correct. No one of these measurements explains the other two.

## Exercise the measurement before spending tokens

In your copy of the config, set `env: {HANDOFF_POLICY: stale}` on the **booker** oracle participant. Choose a policy below. Do not set a policy on a model participant: the policy is a switch in the scripted solution, not part of the task.

| Booker policy | Revision acknowledged | Choice on `replace` / `confirm` | What it checks |
|---|---|---|---|
| `fresh` (default) | Before commit | Correct / correct | A timely update can change the choice. |
| `stale` | No | Wrong / correct | A team can pass without requesting the revision. |
| `late` | After commit | Wrong / correct | A receipt after the decision is not timely. |
| `ignore` | Before commit | Wrong / correct | A timely receipt does not prove use. |
| `unconfirmed` | No, but the world prepared the reply before commit | Correct / correct | No acknowledgement does not imply no delivery. |

The main container records event order, not wall-clock timestamps. The verifier freezes the world before it records closure. The [handoff reader](../../seat_handoffs/README.md) keeps closure separate from the native reward. The public artifacts contain symbolic phase names, IDs, event counts, and decision flags. The private scheduling state is not exported. Seat logs or trajectories can contain private instructions and receipt tokens; do not publish them as anonymized artifacts.

These scripted controls test the benchmark and the reader. They do not establish that a particular model waits, revises its choice, or benefits from an acknowledgement. For model comparisons, pair the same seeds on `confirm` and `replace`, include failed attempts, and report the two variants separately.
