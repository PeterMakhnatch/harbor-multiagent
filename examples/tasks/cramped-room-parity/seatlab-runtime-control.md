# Runtime control: keep both chefs nonroot and the source private

The [original 6 native trials](seatlab-first-run.md) produced 12 permission errors and no team reward. They stay in their original denominator. This registration creates a **separate** 6-trial control with new study and trial identities. No control outcome is known at registration.

## Change only the Harbor runtime

The registered task copy preserves private file modes: `solution/` and `solution/solve.sh` have mode `0700`, and `solution/policy.py` has mode `0600`. Both chefs run as the nonroot user `player`. The original [Olam runtime](https://github.com/olamlabs/harbor-multiagent/blob/6aeb609e31fd77e5f89ce3bcb018db8045520fb7/src/harbor/agents/oracle.py) uploads the solution directory and changes the entrypoint's execute bit as root. That does not make a private parent directory traversable by the seat. All 12 original processes reported `/solution/solve.sh: Permission denied`.

Use the [personal-fork fix at `61023ae7`](https://github.com/PeterMakhnatch/harbor-multiagent/pull/7). It extracts the selected solution as the configured seat user. Do not change the host file modes, run the chefs as root, or alter the world, policies or grader. Run the same frozen task capsule, SHA-256 `2b7961c3e2d7cb17d3af106b54b2e31e68d038aecae015d463725ef37c15aa45`. The task source is [`8b219b95`](https://github.com/PeterMakhnatch/harbor-multiagent/tree/8b219b9597aa699f43d5bba1db212d6a8faa961c/examples/tasks/cramped-room-parity).

## Registrations and endpoints

Use the original [partner-control protocol](seatlab-protocol.md). In order, run A-TT, A-WT, A-TW, B-TW, B-WT and B-TT. Compare each unilateral wait with its own block's TT control. Compare the two TT blocks for repeatability. Do not pool either cohort or replace missing trials. Report how many of 6 trials produced a valid shared grade in each cohort.

Each completed new trial must supply 38 native joint transitions, both client acknowledgments at each tick, 2 role episodes and 1 shared grade. Check the changed actor's first unequal action and the unchanged actor's first unequal input separately. Record actual delivery, not a predicted team outcome. The scripted chefs consult the tick; an acknowledgment does not show that they used the rest of the state.

## Execute with an isolated runtime

Keep the existing interpreter and locked third-party dependencies. Extract the fixed `src/harbor` tree into a private directory. Use a distinct, regular executable wrapper as `--harbor`, with its `PYTHONPATH` pointing only to that extracted `src`. Have it execute the original environment's Harbor console script. The study then records a distinct executable path for every launch without replacing anything in the installed environment. Check the imported `harbor.environments.seat` path before starting.

```sh
python "$FROZEN_TASK/seatlab_study.py" run --harbor "$FIXED_HARBOR" --out "$NEW_STUDY"
python "$FROZEN_TASK/seatlab_study.py" collect --out "$NEW_STUDY" --export "$NEW_EXPORT"
```

Use new output directories. Verify the capsule digest against the original, the study UUID against the registration, and all native UUIDs against both cohorts. Report a missing world log, receipt, or native grade as missing. Do not reconstruct one from the policy tape.
