# Example Configs

- `agents/`: configs that demonstrate specific agent integrations.
- `environments/`: configs that demonstrate environment providers or runtimes.
- `features/`: configs that demonstrate job, trial, artifact, and model backend features.
- `tests/`: task-partition configs for exercising groups of example tasks.

## Cramped Room with two model seats

Run `features/cramped_room_subscription.py` from this checkout. Use the Python
environment for a Harbor runtime with native seat freezes
([#35](https://github.com/PeterMakhnatch/harbor-multiagent/pull/35)).
Set `CRAMPED_ROOM_TASK` to `examples/tasks/cramped-room-parity` from
[#46](https://github.com/PeterMakhnatch/harbor-multiagent/pull/46).
The runner checks the task digest before it starts Docker or calls Codex.
Use local Docker and a current Codex CLI with a ChatGPT subscription login.
Set `CODEX_MODEL` to a native Codex model ID without a provider prefix.

```bash
"${HARBOR_PYTHON:?}" examples/configs/features/cramped_room_subscription.py \
  observe /tmp/cramped-room-observe-1 \
  --task "${CRAMPED_ROOM_TASK:?}" --model "${CODEX_MODEL:?}"
```

On Peter's workstation, prefix the command with `/tmp/crucible-docker-slot.sh`.
Repeat with `repair` and a new output directory. Both modes use fresh model
turns to plan the first 16 actions per seat, then wait 18 ticks before
asking for 4 final actions per seat. Neither mode reuses a saved plan.
Keep the operating system, model, task, and runtime fixed and alternate
which mode runs first.

Both modes read the live board at 3 consecutive ticks: 12, 13, and 14.
At each tick, the seat agent checks whether `chef0`'s current plan would
leave a full, unlit pot that `chef0` can start. `observe` keeps its plan.
In `repair` mode, the first eligible tick triggers 1 model turn to replace
the remaining opening actions: 4 at tick 12, 3 at tick 13, or 2 at tick 14.
Both modes make all 3 observations, even after a repair. There is at most
1 repair turn per trial, and there is none if no tick is eligible.

`receipt.json` uses schema `harbor-cramped-room-subscription/v2`. The `chef0`
`checkpoints` list records `tick`, `eligible`, and `repair_called` for all
3 observations. The `chef1` list is empty. The receipt also shows play time,
seat execution time, and time and tokens for each model turn and seat.
Cached input tokens are part of the input count. `cost_usd` is `null`.
Each valid result must include both native freeze stamps, after play and
before the verifier starts. This does not attest that the shared world
stopped or that the verifier is isolated.

The models get only fixed game instructions and observations. The host
validates game actions before submission. These controls apply to this
example and do not make the game endpoints an access boundary.

## Kit Check with three model seats

The [Kit Check subscription runner](features/kit_check_subscription.py) uses
3 real Codex seats by default: `left`, `right`, and `builder`.
Use `--scripted-reviewers` for a comparison with 2 scripted reviewers and the
same Codex builder. The builder waits up to 150 seconds for both reviews.
Use the [#35](https://github.com/PeterMakhnatch/harbor-multiagent/pull/35)
runtime to record native seat freezes. The studies used `bfbfe127e1`.

### Observed cleanup on the pinned runtime

- **Normal play:** All 6 trials graded valid at 144/144 correct kits.
  In the 3 active trials, the runtime canceled a live left-seat audit.
  The local launchers exited and the observed children were absent before
  grading. All 18 seat freezes finished before grading.
- **Failed play:** The follow-up kept all 6 trials. The 3 healthy trials
  graded valid at 72/72 correct kits. After each of 2 planned builder
  POST faults, the runtime canceled a live left-seat audit.
  The launchers exited and the observed children were absent before the
  last recovery freeze. The remaining trial hit a 120 s right-seat model
  timeout before the planned fault. All 3 failed trials completed recovery
  freezes and had no verifier or reward. The evidence passed integrity
  checks, but the cohort did not meet all acceptance criteria.

These results cover local process cleanup and native seat freezes only.
They do not establish shared-world freeze, verifier isolation, or provider
compute or billing cancellation. Both cohorts used a ChatGPT subscription,
not a paid API. The receipts record `cost_usd` as `null`. Incomplete turns
have unknown token use. Native job totals can omit known completed turns
from a seat whose total usage is unknown.
