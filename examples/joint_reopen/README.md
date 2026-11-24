# Measure whether a team reopens the right decision

Run [Joint Reopen](../tasks/joint-reopen/README.md) when you need a known decision boundary inside a multi-agent task. First the two seats endorse one provisional booking. Then the world releases new private evidence. A team must **keep the booking when it still works** and **revise it when either seat can no longer attend**. The two requirements prevent an always-keep or always-switch policy from looking good by itself.

The task comes from a [failure in archived model teams](../seat_handoffs/README.md#is-first-agreement-a-safe-time-to-stop). That evidence motivates the task. It is not a model result on this new benchmark.

## Run one native team

Work at the root of an installed copy of this Harbor fork, with local Docker available. Use a fresh trial directory. The example uses two separate native `oracle` participants, not LLMs.

```bash
uv run harbor trial start -c examples/joint_reopen/sample-trial.yaml
```

To test regular agents, copy the config. Set each participant's native `name` and `model_name`, keep distinct `seat` values, and remove `JR_POLICY`. Configure credentials through the approved provider adapter outside the task. The world and the verifier need no provider credentials. Record the exact task checksum, roster, exposure limit, and first attempts before a model study. A configured model name is not proof of which model served a turn.

## Distribute a registered study

The [registration](samples/control-registration.json) fixes the task identity, native teams, and all 32 tickets. The [predictions](samples/control-predictions.json) distinguish access to a peer report, its accuracy, the two individual votes, joint feasibility, and team reward. Use the **same manifest bytes and fork revision** for each worker.

```bash
study=$(mktemp -d)
reg=examples/joint_reopen/samples/control-registration.json
uv run python -m examples.joint_reopen.study emit "$reg" --out "$study/east-worker" --shard 0/2
uv run python -m examples.joint_reopen.study emit "$reg" --out "$study/west-worker" --shard 1/2
```

Each worker runs only the files emitted for its shard. These are ordinary Harbor trial configs. Use a new workspace. Do not rerun a started ticket or reuse the same shard on another worker. On a shared host, use your normal Docker concurrency gate. Starting one shard does not start the other.

```bash
for cfg in "$study/east-worker"/configs/*.json; do
  uv run harbor trial start -c "$cfg" || break
done
```

After both workers finish, collect the original first attempts. The cold reader uses only Python's standard library. Omitting a worker must not shrink the denominator.

```bash
python3 -BS -m examples.joint_reopen.collect "$reg" \
  "$study/east-worker/trials" "$study/west-worker/trials" --json
```

Keep the native records private. Publish the redacted outcomes, not raw trajectories, provider configs, or calendars. Preserve valid zeroes, incomplete trials, and native failures separately. Compare channel arms within the same seed, roster and fixture. Withheld content is not the same as a seat receiving a report and ignoring it. No claim about a real model follows from the scripted controls.
