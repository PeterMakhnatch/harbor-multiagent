# Held-out replay and transport stability protocol

This record is prospective. It is separate from the [primary experiment](protocol.md). The fixture describes an information handoff, not a real warehouse or a population of agents.

## Independent native replay

Run 2 independent panels with the same scripted arrival policy, seeds `1001,1003`, conditions `fresh,reordered`, and 12 shipments per trial. Give every attempt a fresh native directory and UUID. Use the pinned [primary implementation](https://github.com/PeterMakhnatch/harbor-multiagent/commit/dc7722833f6d5d4d0726ccbc6755ddaa42faea9b). Compare ordered role inputs, submitted reports, final actions, fingerprints and score. A scalar match alone does not establish replay parity. These are 8 native trials and 2 independent latent seed blocks. Do not include them in the primary denominators. Replaying a hosted decision from a stored action is not a second model generation.

## Fixed-world availability canary

A free inference endpoint can appear in a catalog while it cannot serve a valid, complete cooperative trial. Measure the availability of the whole native operation, not only the endpoint listing. Repeat the same held-out world with seed `1009`, condition `reordered`, and 4 shipments. Each scheduled slot runs one stock-oracle control and one hosted team, sequentially. The route remains `baidu/ernie-5.5-flash-preview:free` at `baidu`, without fallback, and with a maximum price of zero. The hosted team allows at most one generation per active seat per slot: 16 requests across 8 slots. Absent evidence does not count as failure on the task or as zero cost.

The first slot starts at `2026-11-19T09:15:00Z`. The other 7 slots start at 1-hour intervals. The count is fixed before collection. Allow 15 minutes to start a slot. A missed window stays missed. Do not replay missed slots, overlap local native trials, or contact a different provider. Stop if the user requests it or the zero-price route cannot be preserved. If a fixed implementation or contract error invalidates the hosted arm, suspend it and document the reason. Do not count identical broken attempts as new evidence about an endpoint. Scheduler restart must not repeat a trial that already started.

Report eligible, attempted, completed and missed counts separately for each arm. Record the predeclared window, actual start, nested failure stage and provider-reported usage when it exists. Compare canonical fingerprints across native completions. Treat the 8 slots as repeated observations of **one** world on one machine, not 8 independent tasks or a model quality estimate. Hosted output need not be deterministic. The local oracle should have stable role inputs, actions and score.

## Extension boundary

Separate workers can add new worlds, actor-side context transformations, and cross-run readers. Keep world state with the task. Keep observation and analysis formats outside Harbor. Only changes to native runtime semantics belong in the framework itself.