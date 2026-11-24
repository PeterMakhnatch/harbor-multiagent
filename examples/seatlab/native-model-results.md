# Native speaker–listener results

For the direct DeepSeek team experiments, see [Does better team context improve Harbor reward?](live-confirmation.md). It separates a visible notice, a delivered answer, and the final team reward.

## Does sending a short proposal change the native team reward?

**Sending the proposed times did not improve the native team score on these 9 worlds.** All 18 registered pairs completed. Sending the proposals helped 1 pair, hurt 2, and left 15 scores unchanged. The equal-world difference was **−5.56 percentage points**. Each world supplied 2 seat directions. They are not 18 independent worlds.

| Scheduler's first list | Joint meetings with ready marker | Joint meetings with proposed times |
| --- | ---: | ---: |
| Contained the joint start | 6/6 | 6/6 |
| Omitted the joint start | 7/12 | 6/12 |
| **All pairs** | **13/18** | **12/18** |

The first lists were shared within pairs. The split by first list is descriptive. Sending the proposals changed the responder's recommendation set in 8/18 pairs and the submitted meeting time in 5/18. Both losses occurred in world 06. The gain occurred in world 10. Every recommended time was feasible for the responder. It was not necessarily feasible for the scheduler.

The [native register](results/calendar-query-native.json) contains each team's decoded choices and original reward. [Stock Eval Lab's read-only loader](replication.md#read-the-same-native-teams-with-stock-eval-lab) matched all 36 original trial UUIDs and team rewards. The [public calendar check](results/calendar-query-world-check.json) independently graded all 36 submitted times and confirmed 25 successes. The [service check](results/calendar-query-models.json) matched all 108 first replies: 54 from Sol and 54 from Astra. No call used a paid API key.

Run `python3.12 -S -m examples.seatlab.visibility.calendar_query_summary` for the [registered comparison](results/calendar-query-effects.json). Add `--verify-slots` to check the submitted times against both seats' original availability. Run `python3.12 -S -m examples.seatlab.visibility.calendar_query_service` to match the [service receipts](results/calendar-query-service-receipts.json). None of these commands needs Docker or a model.

The [separate fixed-observation replay](https://github.com/PeterMakhnatch/harbor-multiagent/pull/72) showed the scheduler an actual answer to a question. This native study instead sent proposals **to the responder**. It used 9 different worlds. The results do not contradict each other or establish a general effect of messaging. See the [protocol and instructions](replication.md#does-sending-two-proposed-slots-improve-a-native-schedule).

### Pilot included in the final register

**Pilot snapshot, before enrolling the remaining pairs.** Both native teams in world 01, direction 0, submitted slot 11 and earned 1. The scheduler first proposed slots 2 and 3. With the ready marker, the responder returned `[0,11,19,34]`. With the real proposals, it returned `[0,11]`. Neither reply repeated the scheduler's first proposals. The scheduler could still use the recommendations. The host preserved the four-item response despite asking for at most two.

At this point only 1/18 registered pairs had run. Both stock timelines and all 6 model replies passed the independent checks. The [frozen pilot](https://github.com/PeterMakhnatch/harbor-multiagent/blob/2777dd07f3597e82c262d5e73c1e1628c34519ef/examples/seatlab/results/calendar-query-native.json) remains available. The study did not retry or replace this pair.

## Does it matter which role gets the calendar tool?

In the first [registered block](replication.md#which-teammate-should-receive-a-local-tool), 4 native teams completed. When only the sender had access, the blind room scored 1 and the peer room scored 0. When only the receiver had access, both rooms scored 0. The host reconciled 24 direct calls at **$0.001151628 USD**. This is one world, direction, and repetition. It does not establish which role should receive the tool.

**Moving the tool to the receiver added 3 agreements across 96 matched comparisons.** The [full register](results/role-allocation-live.json) includes 192 first-attempt Harbor teams in 12 fixed worlds. All teams submitted a valid slot. Of 96 pairs, receiver access won 17, lost 14, and tied 65. The registered equal-world difference was **+3.125 percentage points**. This small difference does not identify a general best role for the tool.

| Room | Agreement with sender tool | Agreement with receiver tool |
| --- | ---: | ---: |
| Blind | 11 / 48 | 12 / 48 |
| Peer list visible | 6 / 48 | 8 / 48 |

The [stock Eval Lab reader](results/role-allocation-stock.json) confirmed all 192 rewards and found 384 native seat records. Those records have no model token counts. The separate [host ledger](results/role-allocation-cost.json) matched 1,152 direct calls at **$0.051333798 USD**, including the first block. No hold remains. The 2 rooms share opening messages; they are not 2 independent worlds.

## What ran

The run measured **12 of 16 registered native Harbor trials**. In the 3 complete seed and delivery-order groups, the inventory model copied both revisions correctly. The dispatch model changed choices in both `reordered` groups, but did not always improve the number of optimal choices.

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

### Did the model choose as if it used the last, older report?

**Not on a single choice that distinguishes the rules.** In the 4 measured `reordered` teams with a model in dispatch, newest and last recommended different bays on 14 decisions. The model chose the newest report's best bay 12 times, the older report's best bay 0 times, and neither 2 times. The 14 decisions repeat **7 distinct shipments across 2 worlds**. They are not 14 independent trials.

On the other 34 decisions, the rules recommended the same bay. The model chose that bay 32 times and a different bay twice. All 4 misses came from one native team, `2003 / SM`. The full study contains 16 registered teams: 12 measured, 1 invalid and 3 not attempted. None of the missing grades enter these counts.

```sh
python3.12 -BS -m examples.seatlab.dispatch_opportunity observed
```

This command reads the [saved study](results/native-model-primary.json). It checks the native world fingerprint, both actors, sent-report fidelity, delivered-message digests and per-shipment grades. It emits counts for each `reordered` team. A wrong inventory report is not counted as a receiver choosing the older report. The released projections omit incorrect payloads, so the reader cannot reconstruct those decisions.

Exit `3` preserves measured rows when the study is incomplete. Exit `0` requires a fully measured grid. Exit `2` rejects an invalid input. To inspect a new `team_report.collect` return of the same registered protocol, add `--report FILE --sha256 HELD_DIGEST`. A matching choice shows consistency with a rule. It does not prove how the model read the reports.

### Check one private native trial

```sh
python3.12 -BS -m examples.seatlab.dispatch_opportunity trial "$trial_dir" \
  --trial-id "$expected_uuid" --require-bound
```

Use your own completed trial directory. Take the expected UUID from your job record. The reader checks both native role results, the verifier reward and the regenerated stock world. It reports three separate counts: choices consistent with truthful reports, choices consistent with actual delivered reports, and the subset where inventory sent an incorrect report. It does not print a prompt, an answer, or a host path.

`observations` gives the evidence for each active role. `bound` means the client acknowledged the matching view digest. `reconstructed` means only the world record supports the view. Neither proves that a model used the view. Use `--require-bound` to require matching receipts for all active roles. A contradictory receipt is invalid, not reconstructed.

Exit `0` means the requested comparison is available. Exit `3` covers missing evidence, missing required acknowledgments, or a different condition. A completed non-`reordered` trial retains its actual grade but has no order comparison. Exit `2` rejects inconsistent or invalid evidence. These are local, unsigned records. The UUID and the checks do not authenticate their producer.

In this study, the direct reader reproduced all 8 reorder comparisons and all 4 fresh grades. Both role receipts matched in all 12 completed teams. The reader rejected the invalid team. The other 3 teams had no recorded attempt.

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

All 72 calls returned a valid first response. They repeat 6 source trials, not 72 independent tasks. The [per-call cost record](results/visibility-label-crossover-cost.json) gives a token-based estimate of **$0.001641600 USD**, with no unresolved reservation.

### How many distinct numerical problems did these trials contain?

**Six archived receiver cases contained only 2 distinct underlying problems.** We checked every attribute value in the private stock record. We also compared the 3 numerical reports the receiver actually fetched. We kept each reporter and attribute fixed. A simultaneous reversal of the route labels did not create a new input.

| Source | Authenticated rounds | Distinct true tables | Distinct claim patterns | Distinct patterns with opposing reports |
| --- | ---: | ---: | ---: | ---: |
| Original listener panel | 6 | 2 | 4 | 2 |
| Full native archive, including the original panel | 180 | 6 | 15 | 6 |
| Mixed claims and true tables both absent from the original panel | 60 | 4 | 4 | 4 |

Two reports oppose each other when their own numerical totals strictly favor different routes. None of the 180 rounds had a mean route-score difference within 5 points. An authenticated round is not an independent problem or a new team.

**Decision: do not buy more receiver samples to claim generalization from this archive.** The [registered holdout rule](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7062533433) required 8 new true tables and 8 new mixed claim patterns. The archive supplied 4 of each. This does not rule out a report-order effect. Use new task instances to test it across different problems.

To check your retained copy, run `python -m examples.seatlab.visibility.listener_coverage "$CRUCIBLE_ROOT"` from the fork. The command reads the archive and returns [aggregate counts](results/visibility-listener-coverage.json). It makes no model call and starts no container.

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
