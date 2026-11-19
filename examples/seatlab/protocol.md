# Dispatch Handoff: protocol before data collection

Question: when an inventory agent reports changing capacity, does a dispatch agent select the highest-value bay that is still available? This is a protocol test on synthetic shipments. It is not a model leaderboard.

## Why this task

[Project EVE](https://www.anthropic.com/research/project-eve-claude-plays-mmorpgs) describes agents making decisions from stale or unchecked shared knowledge. [Patterns and problems in multiagent systems](https://www.anthropic.com/research/multiagent-systems) distinguishes a team's shared discussion from its final integrated result. The [Multi Agent Coordination Bench report](https://benchmarks.bespokelabs.ai/multi-agent-coordination-bench/) distinguishes a communicating group from a silent group. We borrow these questions, not their tasks, data or reported effects.

Both native Olam seats share one world. Inventory can see availability but not utilities. Dispatch can see utilities but not actual availability. One group reward scores the committed choice. No judge model reads a transcript. A visible message shows what the world provided, not what the model noticed or believed.

## Primary controls

| Condition | Information that dispatch receives |
| --- | --- |
| `silent` | No inventory report. Inventory still performs its normal task. |
| `stale` | The inventory agent's revision 1 report only. |
| `fresh` | The inventory agent's revision 2 report only. |
| `reordered` | Revision 2 followed by revision 1. |
| `pooled` | The actual revision 2 availability, without the inventory agent's report. A second native seat is present but has no decision to make. |

Predeclared primary seeds: `17,29`. Each seed has `12` shipments, four ordered utility values and two feasible bays per shipment. Half the shipments retain their original availability. The other half replace one available bay. All conditions use the same hidden scenarios for a given seed. The primary matrix uses the stock oracle in each active seat, with all five conditions. The pooled arm is an information ceiling, not a compute-matched single-agent baseline.

Primary outcome: the number of shipments with the unique optimal feasible choice. Secondary outcome: the number with any feasible choice. Report both numerator and denominator. Split by stable versus changed availability. Pair by seed, policy, episode and a fingerprint of the underlying scenarios. Report native trials and shipments separately. Never treat multiple observations from one batch as independent trials. Missing evidence is not a zero. Do not select seeds because of their scores.

Before observing results, we expect `fresh`, revision-aware `reordered` and `pooled` to agree on all shipments for truthful, deterministic agents. `stale` must match them on stable shipments but need not on changed ones. The oracle has no access to hidden availability in the silent arm. The reference decisions prove the implementation can express these differences; they do not measure learned behavior.

## Stress tests and model pilot

1. **Arrival order.** On the same seeds and batches, replace highest-revision selection with a consumer that uses the last report in the array. Compare `stale`, `fresh` and `reordered`. In `reordered`, its choices should equal the stale choices shipment by shipment, not just in aggregate. Investigate any disagreement before attributing it to context.
2. **Independent composition.** Enumerate all utility permutations and availability transitions under uniform weighting. Keep these design expectations separate from actual Harbor observations.
3. **Transport pilot.** If a fixed, subscription-only, tool-free hosted route can be attested, try seeds `101,103`, 4 shipments per batch. Start with `fresh` and `reordered`. One generation per active seat per native trial; no retries to improve scores. Report configured and served model identity, attributable usage when available, and malformed or unfinished generations. Do not generalize this pilot to other models or tasks. If the route cannot be attested, report no model measurement.
4. **Replication.** Use seeds starting at `1001`, not either primary seed. Specify the conditions and reason in a separate record before executing them. Keep implementation repairs and reruns separate from first attempts.

## Runtime and retention

The initial study targets Olam commit `6aeb609e31fd77e5f89ce3bcb018db8045520fb7`, not stock Harbor 0.26. Run the maintained `hello-seats` task as an independent positive control first. Use local Docker with at most 1 trial at a time for this run. No cloud provisioning or metered API is required for the controls. Use fresh output directories. Keep all failed attempts and their reason in the study record. Keep host paths, personal credentials and raw provider traces out of public evidence. Publish to the personal fork only.
