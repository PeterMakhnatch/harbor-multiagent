# Native speaker–listener results

The run measured **12 of 16 registered native Harbor trials**. In the 3 complete seed and delivery-order groups, the inventory model copied both revisions correctly. The dispatch model changed choices in both `reordered` groups, but did not always improve the number of optimal choices.

## Does it matter which model handles inventory?

The [registered mixed-roster study](replication.md) swaps a direct DeepSeek seat and a Claude Max subscription seat in native Harbor. The first team used Claude for inventory and DeepSeek for dispatch. Both seats completed. The native score was 1.0 on 6 shipments. The direct call matched the host ledger at **$0.000098700 USD**, with no open hold. One team does not establish a model or role effect.

## What ran

The [frozen protocol](team-protocol.md) registers seeds `2001` and `2003`, 2 delivery orders, and 4 actor pairings. Each trial has 12 synthetic shipments. `fresh` delivers revision 2 only. `reordered` delivers revision 2, then revision 1. The first letter in each pairing identifies inventory (speaker); the second identifies dispatch (listener). `S` is the arrival-order script, not the oracle. `M` is `anthropic/claude-sonnet-5-5`, served by the first-party Claude CLI.

The run attempted **13 cells: 12 measured and 1 invalid**. It stopped at `s2003-fresh-MM`. The projection marks dispatch `invalid_or_refused_decisions`; it does not distinguish between those outcomes. The cell has no accepted measurement. The other `2003 / fresh` cells (`MS`, `SM`, `SS`) were not attempted. The run did not retry or replace any cell. Missing measurements do not count as zero scores.

## Complete pairs

Each entry shows **choices changed / change in optimal choices / change in feasible choices**, out of 12 shipments. The arrow shows the substitution from script to model. All 12 complete contrasts have equal delivered messages; 4 registered contrasts are unavailable.

| Seed / delivery order | Dispatch: `SS` → `SM` | Dispatch: `MS` → `MM` | Inventory: `SS` → `MS` | Inventory: `SM` → `MM` |
| --- | ---: | ---: | ---: | ---: |
| `2001 / fresh` | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| `2001 / reordered` | 4 / +4 / +2 | 4 / +4 / +2 | 0 / 0 / 0 | 0 / 0 / 0 |
| `2003 / reordered` | 5 / −1 / +3 | 3 / +3 / +3 | 0 / 0 / 0 | 4 / +4 / 0 |
| `2003 / fresh` | — | — | — | — |

- **Dispatch:** All `2001 / fresh` cells reached 12 optimal choices. In `2003 / reordered`, `SS` → `SM` gained 1 optimal choice and lost 2, even as feasibility rose from 9 to 12. With model inventory in that group, `MS` → `MM` raised both counts from 9 to 12.
- **Inventory:** Both actors matched their own native input on all 144 revision reports per actor. In `2003 / reordered`, `SM` → `MM` changed 4 choices with equal messages and the same accepted dispatch view digest. The 2 cells used separate dispatch model generations. This contrast does not show a speaker effect through the messages.

## Model resources

The table sums the CLI fields by role. CLI and API durations are sums across calls, not trial elapsed time.

| Cells | Model role | Calls | Input tokens | Cache creation tokens | Cache read tokens | Output tokens | CLI seconds | API seconds | CLI estimate (USD) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Measured | Inventory | 6 | 12 | 4,861 | 2,676 | 3,444 | 30.620 | 27.595 | 0.0541756 |
| Measured | Dispatch | 6 | 12 | 6,011 | 3,825 | 1,213 | 20.063 | 16.977 | 0.0365805 |
| Invalid | Inventory | 1 | 2 | 1,256 | 0 | 574 | 5.842 | 5.746 | 0.0107680 |
| Invalid | Dispatch | 1 | 2 | 1,510 | 0 | 381 | 3.337 | 2.905 | 0.0098540 |

The 6 measured script seats per role used 0 model turns. The run recorded 14 model turns: 12 in measured cells and 2 in the invalid cell. All 14 have a known CLI estimate, for a total of **$0.1113781 USD**. The protocol uses the existing Claude Max subscription with estimate limits of $0.30 USD per call and $4.80 USD total. The estimates are not an invoice or an incremental subscription charge. Local Docker instances did not incur a usage charge.

## Can stock Eval Lab read these trials?

Yes, through the raw trial reader. The [optional bridge](team_census.py) called stock `evallab.results.load_trial` on each attempted standalone trial. It found **13 native trial UUIDs, 26 seat records, and 12 known team rewards**. It did not duplicate the team reward across seats. The invalid trial has no accepted team score. The 3 unattempted trials have no seat token measurements.

The same bridge opened the stock census read-only on exactly those directories. All 13 census rows matched the raw path and trial UUID. None had a native job UUID, so the census correctly declined to project all 13 rewards. A standalone trial is not a Harbor job. The derived directory remained empty. This result does not describe trials launched under a native job.

| Native seat field | Inventory | Dispatch |
| --- | ---: | ---: |
| Seats with known input, cache, and output counts | 13 | 13 |
| Registered seats with unknown counts | 3 | 3 |
| `n_input_tokens`, summed over known seats | 8,807 | 11,360 |
| `n_cache_tokens`, summed over known seats | 2,676 | 3,825 |
| `n_output_tokens`, summed over known seats | 4,018 | 1,594 |

The native input field includes the CLI input, cache creation, and cache read counts. Do not add the cache column to native input. Totals include the invalid attempt's actual usage. See the [path-free census projection](results/native-model-census.json) for each registered slot. To reproduce on retained private trials, run `python examples/seatlab/team_census.py --study STUDY --evallab-root EVAL_LAB` with Eval Lab installed. No model, network service, or database backfill is needed.

## What the evidence supports

The report checks actor identity, served model metadata, accepted role-view digests, report fidelity, delivered messages, and committed choices. The independent native verifier scores feasibility and optimality without a model judge. These checks test the recorded model actions against deterministic evidence. They do not prove that a model understood its view or will repeat its choices. With 2 seeds, 1 missing group, and no repeated generation per seat, the results do not establish a general causal effect or rank models.

Sources: [machine-readable results](results/native-model-primary.json), [frozen protocol](team-protocol.md), [study driver](team_driver.py), [native model agent](native_model_agent.py), and [evidence and contrast code](team_report.py).

## What the 616 stored trials can tell us about team communication

We removed incoming peer messages from one receiver's recorded view at a time. This cost $0 and changed no original trial. A fetch counts only if the server returned a peer message to that receiver. “Before action” requires a later recorded action by **the same receiver**. We did not substitute another seat's action or count an unread post as a fetch.

| Population | Trials | Multi-seat trials | Trials with a changed fetch | Of these, before an action | Receiver interventions before an action |
| --- | ---: | ---: | ---: | ---: | ---: |
| [Flotilla](results/visibility-flotilla.json) | 276 | 180 | 83 | 81 | 142 |
| [Muster](results/visibility-muster.json) | 210 | 120 | 30 | 30 | 83 |
| [Crucible](results/visibility-crucible.json) | 130 | 124 | 122 | 122 | 303 |

The clearest contrast is within Muster. All 30 talking teams fetched a peer message before a later action. The 180 other trials had no team channel. In Flotilla, 89 trials made a message available, but only 83 recorded a fetch. The result depends on which event the recorder captures.

**None of these archives binds a peer message to an exact model request.** The counts show what the receiver fetched, not what the model used. No archived score estimates what would happen with a silent team. The [8 separate Crucible timeouts](results/visibility-crucible-timeouts.json) are outside the 616. Seven still recorded a peer fetch before an action. Dropping failed trials can drop evidence of team behavior.

The [replay instructions](replication.md#replay-the-original-server-evidence) include the expected archive roots and the adapter contract for a new task. The public results contain counts, not messages, seat IDs, or private trial paths.

### What the original teams did

In Muster, 3 agents opened boxes on 10 task instances. Each instance had 3 attempts with chat and 3 without chat. With chat, teams less often opened a box another seat had already opened.

| [Muster boxes](results/visibility-muster-behavior.json) | Without chat | With chat |
| --- | ---: | ---: |
| Native trials | 30 | 30 |
| Mean reward, equal weight per instance | 0.515 | 0.857 |
| Mean share of opens that repeated another seat's open, equal weight per instance | 39.8% | 18.1% |
| Repeated opens / all accepted opens | 321 / 806 | 146 / 791 |

All 90 talking seats fetched a peer message. Of their 791 accepted opens, 163 preceded that seat's first fetch. Of those 163, 26 repeated a peer's open. This is a comparison of original team behavior, not a rescore after removing chat.

In [30 original Crucible teams](results/visibility-crucible-variation.json), decision makers chose the correct route in 177 of 180 rounds. We matched different trials by model, scout policy, round, reporting seat, numeric claims, and report order. Among 151 matched pairs, the route differed in 1 and the flagged scout differed in 29. Ignoring order admitted 699 pairs; the route differed in 27 and the flag differed in 150. These pairs share source trials. Equal numeric evidence does not establish equal prompts, prior rounds, or model requests.

### Did new listeners use the archived reports?

We made 36 fresh, isolated requests to direct `deepseek-flash`. Each of the 6 selected historical cases had 3 repeats per arm. All requests returned a valid choice on their first attempt. Without reports, the model chose A on every request. A happened to be correct in 4 of the 6 cases.

| [Standalone listeners](results/visibility-listener.json) | No reports | Visible reports |
| --- | ---: | ---: |
| Correct choices | 12 / 18 | 12 / 18 |
| Choices of A | 18 / 18 | 12 / 18 |
| Within-case repeat pairs with different choices | 0 / 18 | 4 / 18 |

The arms differed on 6 matched choices. Three favored the visible reports and 3 favored no reports. The requests used independent samples. We cannot tell which single response would have changed, or assign a new score to an archived team.

The [per-request cost ledger](results/visibility-listener-cost.json) records 36 settled calls, 4,752 uncached input tokens, and 180 output tokens. The token-based estimate was **$0.000820800 USD**, with no unresolved reservation. This is an estimate at the direct provider's listed rate, not an invoice.

### Did the listener follow the evidence or keep the same letter?

We ran [72 new requests](results/visibility-label-crossover.json). Each case appeared under both its original labels and an exact swap of A and B. If the listener selects a *different* letter after the swap, it selects the *same physical route*.

| Result across 6 archived cases | No reports | Visible reports |
| --- | ---: | ---: |
| Same physical route under both label maps | 0 / 18 pairs | 13 / 18 pairs |
| Correct route across both label maps | 18 / 36 choices | 23 / 36 choices |
| Chose the displayed letter A | 36 / 36 choices | 23 / 36 choices |

The no-report prompt was identical under both maps. The listener always chose A. With reports, its choices followed the physical route in 13 pairs, but still kept the same letter in 5. This separates label preference from sensitivity to evidence. It does not show that the listener always interprets the reports correctly.

All 72 calls returned a valid first response. They repeat 6 source worlds, not 72 independent tasks. The [per-call cost record](results/visibility-label-crossover-cost.json) gives a token-based estimate of **$0.001641600 USD**, with no unresolved reservation.

### Did current inventory reports help a new Harbor team?

Yes, in all 4 registered Dispatch Handoff worlds. A direct `deepseek-flash` agent reported inventory. A second agent chose a loading bay for each of 4 shipments. Both agents ran in both arms. The `fresh` arm delivered the inventory reports. The `silent` arm delivered none. Neither model saw the arm name.

| World seed | Complete pairs | Fresh minus silent: share of optimal choices |
| --- | ---: | ---: |
| 3101 | 2 | +0.50 |
| 3103 | 3 | +0.50 |
| 3109 | 3 | +0.50 |
| 3119 | 3 | +0.25 |

The [registered result](results/visibility-native-pairs.json) gives each world equal weight: **+43.75 percentage points**. The dispatcher selected a different bay on 19 of the 44 matched shipments. Within the same world and arm, no choice differed across repeated runs: 0 of 40 comparisons with reports and 0 of 48 without them. These comparisons reuse shipments. They are not independent tasks.

All 11 valid `fresh` teams chose the optimal bay for all 4 shipments. The native verifier scored the accepted choices against the final inventory. These results measure the value of this channel in this task. They do not show that chat helps teams in general.

We attempted all 24 trials. A launcher error stopped `s3101-r0-fresh` before either seat made a paid call. We fixed the launcher, kept the missing result, and did not retry it. The [per-seat cost record](results/visibility-native-cost.json) covers all 46 paid calls in the 23 valid trials. The listed token rates give **$0.003868956 USD**: 4,352 cached input tokens, 8,594 uncached input tokens, and 4,278 output tokens. No call has an unresolved reservation.

Stock Eval Lab read 23 native team rewards and 46 submitted seat actions. None of those seats had native token counts. The [read-only comparison](results/visibility-native-usage.json) found a settled direct receipt for each of the 46 actions. It did not fill missing native fields from those receipts. The 2 seats in the failed launch remain unknown to the native reader.

### Did asking for a particular missing report help?

**First of 3 registered service windows.** Both arms made 2 new receiver calls and delivered 1 real sender update per session. In one arm, the model chose which shipment to refresh. In the other, the queue delivered an unread update.

| First window, 4 source worlds | Model selects | Unread-first queue |
| --- | ---: | ---: |
| Sessions with an unread update delivered | 9 / 24 | 24 / 24 |
| Optimal shipment choices | 76 / 96 | 86 / 96 |
| Feasible shipment choices | 79 / 96 | 87 / 96 |

The model spent 15 of its 24 selections on an update it already had. With equal weight per world, its optimal-choice rate was 10.4 percentage points below the queue in this window. The requests ran from 4:10 PM ET to 4:12 PM ET on November 20. This is not the 3-window result. These are new receivers acting on frozen sender reports, not new native team scores.

The [first-window choices](results/clarification-window0.json), [cost ledger](results/clarification-window0-cost.json), and [timing reconciliation](results/clarification-window0-meter.json) are public. All 96 calls started inside the registered window and settled. The price estimate is **$0.005385600 USD**, not an invoice. The [protocol](replication.md#can-a-receiver-choose-which-missing-update-to-request) explains how to repeat the study.

**Second service window.** The model-selected arm delivered an unread report in 10 of 24 sessions. The queue did so in all 24. The model-selected arm made 76 optimal choices out of 96. The queue made 83. The matched difference was −7.3 percentage points with equal weight per world. Both windows use the same 4 worlds.

All 96 calls started and finished between 7:11 PM ET and 7:14 PM ET on November 20. A launcher check had compared an empty array to `0` and exited before the first cell. We checked the empty ledger and untouched cell register before starting inside the original window. No cell was retried. The [choices](results/clarification-window1.json), [cost ledger](results/clarification-window1-cost.json), and [timing reconciliation](results/clarification-window1-meter.json) cover these first attempts. Estimated cost: **$0.004294872 USD**.
