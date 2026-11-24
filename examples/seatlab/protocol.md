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

**Prospective transport amendment (before any model call):** Use an explicitly free API route instead of a subscription login. The primary route is `baidu/ernie-5.5-flash-preview:free` at provider `baidu`. Require zero quoted input and output prices, an explicit provider, no fallback and a server-side maximum price of zero. Admit only the seat's synthetic view as input. The model can return JSON decisions, not tools or shell commands. Keep the previously declared seeds, treatments, denominators and one-generation limit. An unavailable route is an operational outcome, not permission to switch models within the panel.

## Review a Dispatch panel without running it

Use Python 3.12 or later. The coordinator can register jobs and a second worker can check them without Harbor, Docker, a key, or a provider catalog.

```sh
root=$(mktemp -d)
panel=examples.seatlab.run_panel
python3.12 -BS -m "$panel" --plan-only --out "$root/held" --policy arrival \
  --seeds 17,29 --conditions fresh,reordered --episodes 12 --jobs 1
packet="$root/held/registration.json"
pin=$(python3.12 -BS -c 'import hashlib,sys; print(hashlib.file_digest(open(sys.argv[1], "rb"), "sha256").hexdigest())' "$packet")
python3.12 -BS -m "$panel" --check-plan "$packet" --sha256 "$pin"
```

Keep the digest outside the worker's write access. Transfer the packet and provide the trusted digest separately. `--check-plan` checks the exact bytes against that digest. It also checks every task source, both role instructions and the registered panel runner in the worker's checkout. A hosted packet also includes the seat adapter. A copied packet does not authorize a launch.

The report separates native teams, seat slots, shipment evaluations and maximum hosted calls **by role**. For example, 2 seeds, `fresh,pooled` and 12 shipments give 4 teams, 8 seat slots and 48 evaluations. Under `hosted`, only 2 inventory seats and 4 dispatch seats can call a model. Each request permits 3,008 output tokens, or 18,048 in total. `pooled` uses a no-op inventory seat. Cases repeat across arms. The report does not predict input tokens, actual completions, runtime or price. It does not check that the route is online.

After separate authorization, `--from-plan "$packet" --sha256 "$pin" --harbor EXECUTABLE --out NEW` is the mode that starts Harbor. Use the site's concurrency guard. This mode takes the route, cases and job limit from the packet; it rejects new design flags. The hosted seat rejects a different case count before it can request a completion. Older runs retain their original plans and source checkout.

### Can the inputs distinguish the intended behavior?

Run this before allocating seats. It reads the registered `fresh` and `reordered` inputs, not any seat choices. Nothing is submitted to the world.

```sh
python3.12 -BS -m examples.seatlab.dispatch_opportunity design "$packet" --sha256 "$pin"
```

The comparison asks whether the **newest revision** and the **last delivered report** recommend different best bays, assuming truthful inventory reports. In the registered seeds `17,29`, only **10 of 24 unique shipments** distinguish these rules. Using the older report would make 7 choices infeasible and 3 feasible but suboptimal. The other 14 shipments cannot distinguish the rules by their optimal choice, even though availability changed in 2. The 2 arms expose the same 24 shipments twice; they do not create 48 independent opportunities.

Exit `0` means at least one shipment distinguishes the rules. Exit `1` means none do. Exit `3` means a comparison arm is absent; the denominators stay null. Exit `2` rejects an invalid packet or changed source. This is a design check, not a team reward, a forecast of model behavior, or a reason to select favorable seeds.

The [retained native study](results/native-model-primary.json) provides an independent check with different seeds. At seed `2001`, its truthful arrival-order scripts scored 12 optimal and 12 feasible in `fresh`, then 8 optimal and 10 feasible in `reordered`. At seed `2003`, they scored 9 optimal and 9 feasible in `reordered`. All 3 results match the input calculation. The `2003 / fresh` script team never ran.

After a team returns, use the [retained-choice diagnostic](native-model-results.md#did-the-model-choose-as-if-it-used-the-last-older-report). It compares actual decisions, not just the opportunity to distinguish them.

### Isolate report order without changing what the sender said

**Question:** Does a receiver choose a different bay when the same two peer reports arrive in the opposite order? The native `fresh` arm omits the old report. A `fresh` versus `reordered` grade alone cannot isolate order.

Start with the `$packet` and `$pin` from [the registration above](#review-a-dispatch-panel-without-running-it). These commands generate inputs only. They do not start a receiver.

```sh
order() {
  mode=$1; shift
  python3.12 -BS -m examples.seatlab.dispatch_order "$mode" \
    --registration "$packet" --sha256 "$pin" "$@"
}
order prepare > "$root/order-plan.json"
order_pin=$(python3.12 -BS -c 'import json,sys; print(json.load(open(sys.argv[1]))["panel_sha256"])' "$root/order-plan.json")
order emit --panel-sha256 "$order_pin" --wave 0 > "$root/wave-0.jsonl"
order emit --panel-sha256 "$order_pin" --wave 1 > "$root/wave-1.jsonl"
order score --panel-sha256 "$order_pin"  # exit 3: no first replies yet
```

The example has 2 worlds, 24 distinct shipments and **4 first replies**. In 10 shipments the two reports recommend different best bays. The input is otherwise identical across arms, including both report payloads, available actions, utilities, and instructions. Each wave contains one input per world. The first ordering alternates across worlds. Add `--shard 0/2` or `--shard 1/2` to split a wave between workers.

Send only `messages` to a fresh receiver. Give each request its own session. Return a JSON line with `id`, `prompt_sha256`, and the first raw `reply` string. Do not retry a malformed reply. Keep the plan digest with the coordinator. The worker does not receive the ordering label, seed, actual stock, old choices, or native reward.

After authorized calls, use `order score --panel-sha256 "$order_pin" first-*.jsonl`. The report separates choice changes, agreement with the newest **reported** stock, and correctness against **actual** stock. Only complete world pairs contribute to counts. Missing and invalid replies stay visible. A worker file with a duplicate subject or mismatched digest is rejected. Different decisions do not, by themselves, establish an order effect.

To use a real inventory seat's reports, replace `--registration … --sha256 …` with `--trial "$trial_dir" --trial-id "$expected_uuid"`. Repeat the arguments for distinct worlds. The reader requires a matching dispatch view acknowledgment and a valid native result. It retains incorrect peer reports. It regenerates the replay prompt, not the original model session. In the retained `2001` and `2003` worlds, the same check found 7 informative shipments among 24. No altered order has a measured team reward.

#### Compare different orderings against repeated answers to identical prompts

If the receiver can vary its answer, two different decisions do not isolate an order effect. Register `--draws 2` before distributing any work. For the example above, this registers 8 first replies across 2 worlds. It does not make any calls.

```sh
order prepare --draws 2 > "$root/repeated.json"
repeat_pin=$(python3.12 -BS -c 'import json,sys; print(json.load(open(sys.argv[1]))["panel_sha256"])' "$root/repeated.json")
for wave in 0 1 2 3; do
  order emit --draws 2 --panel-sha256 "$repeat_pin" --wave "$wave" > "$root/repeat-$wave.jsonl"
done
order score --draws 2 --panel-sha256 "$repeat_pin"  # exit 3: 8 replies missing
```

Each ordering gets 2 fresh sessions with identical messages and different request IDs. In successive waves, one world uses chronological, reversed, reversed, chronological. The next world uses the opposite sequence. Give the receiver only its own messages. Keep the model, settings and first-reply policy fixed. The scorer cannot verify which model or session produced a reply.

For each shipment, the scorer compares both pairs with identical inputs and all 4 pairs with opposite orderings. It reports disagreement on all shipments and on the subset where the two reports recommend different bays. These comparisons reuse answers. They are not independent trials. The primary descriptive contrast is the cross-order disagreement rate minus the same-order disagreement rate, averaged with equal weight per world. It can be negative. The full-panel contrast stays null until every registered world returns all 4 valid replies. Worlds with no informative shipments do not enter the informative contrast. This is not an estimate of team reward.

#### Reserve work once when multiple operators share a machine

`emit` previews inputs and can repeat them. Do **not** feed its output to a launcher that retries. For handoff, use `claim` with one private directory on a trusted local POSIX filesystem. Keep that directory after a coordinator restart. Every operator for this panel must use the same directory and the same panel digest.

```sh
umask 077
mkdir -m 700 "$root/claims"  # create once, under a trusted parent
out=$(mktemp "$root/handoff.XXXXXXXX")  # never truncate a previous handoff
order claim --draws 2 --panel-sha256 "$repeat_pin" --wave 0 --shard 0/2 \
  --spool "$root/claims" --max-requests 8 --max-input-bytes 65536 > "$out"
order status --draws 2 --panel-sha256 "$repeat_pin" --spool "$root/claims"
```

The request and byte limits apply to the **whole registered panel**, even for one shard of one wave. `prepare` reports the UTF-8 byte count of the canonical message arrays. This is not a token count or a price. `claim` exits `1` without reserving or printing a request if either limit is too low. It does not contact a provider.

`claim` persists a subject reservation before printing a worker request. If operators contend for the same subject, only one gets its request. A repeated claim succeeds with empty output for subjects already reserved. A crash after reservation but before delivery leaves an unresolved reservation. This prevents automatic duplicate handoffs; it does **not** prove delivery, payment, exactly-once execution, or model identity. Do not delete claims or create another spool to retry.

Supply trusted first-reply files to `status` to distinguish reserved-but-missing, invalid, and valid replies. It also shows replies without a local reservation. It prints only subject IDs, block and wave numbers, and states. Exit `0` means every subject has both a reservation and a valid first reply. Exit `1` means at least one reply has no reservation. Exit `3` means coverage is incomplete. Exit `2` rejects malformed receipts or an unsafe spool. Status is a read-only snapshot, not permission to resubmit. Use one coordinator spool for remote workers; separate machines or copied spools cannot deduplicate each other.

## Runtime and retention

The initial study targets Olam commit `6aeb609e31fd77e5f89ce3bcb018db8045520fb7`, not stock Harbor 0.26. Run the maintained `hello-seats` task as an independent positive control first. Use local Docker with at most 1 trial at a time for this run. No cloud provisioning or metered API is required for the controls. Use fresh output directories. Keep all failed attempts and their reason in the study record. Keep host paths, personal credentials and raw provider traces out of public evidence. Publish to the personal fork only.
