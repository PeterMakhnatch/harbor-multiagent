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
Use a current Codex CLI with a ChatGPT subscription login.

```bash
"${HARBOR_PYTHON:?}" examples/configs/features/cramped_room_subscription.py \
  observe /tmp/cramped-room-observe-1 \
  --task "${CRAMPED_ROOM_TASK:?}" --model "${CODEX_MODEL:?}"
```

On Peter's workstation, prefix the command with `/tmp/crucible-docker-slot.sh`.
Repeat with `repair` and a new output directory. Both modes create new opening
plans and read the live board at tick 12. `repair` adds 1 model turn only when
`chef0` is about to leave a full, unlit pot that it can start. Neither mode
uses a saved plan. Keep the operating system, model, task, and runtime fixed
and alternate which mode runs first.

Read `receipt.json` for play time, the checkpoint result, and time and tokens
per seat. Cached tokens are part of the input count. The subscription reports
no price in dollars. Each result must include both native freeze stamps,
after play and before the verifier starts. This does not attest that the
shared world stopped or that the verifier is isolated.

The models get only fixed game instructions and observations. The host
validates game actions before submission. These controls apply to this
example and do not make the game endpoints an access boundary.
