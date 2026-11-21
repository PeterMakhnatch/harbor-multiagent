# Native speaker–listener results

The run measured **12 of 16 registered native Harbor trials**. In the 3 complete seed and delivery-order groups, the inventory model copied both revisions correctly. The dispatch model changed choices in both `reordered` groups, but did not always improve the number of optimal choices.

## Can a reviewer fix a draft that conflicts with a teammate’s answer?

This test starts from the 8 actual drafts in the [native Dispatch answer study](results/dispatch-answer-transfer.json). Each reviewer gets the same 6 shipments, old inventory reports, 2 delivered answers and original draft. A general review asks for 6 final choices. A specific review also names each draft choice that conflicts with a negative answer. Neither review reveals current inventory or the best choices.

The [registration](https://github.com/PeterMakhnatch/harbor-multiagent/pull/76#issuecomment-7058848219) fixes 2 first attempts per arm and world. The comparison uses 8 worlds, not 192 independent shipment choices. These are new receiver decisions on stored team messages, not new Harbor team rewards.

**Naming conflicts helped reviewers obey the answers. It did not improve the shipment score.** All 32 first responses were valid. The original drafts had 7 conflicts and 22 optimal choices across 48 shipments. Each arm reviewed every draft twice.

| Across 16 reviews per arm | General review | Review that names conflicts |
|---|---:|---:|
| Original conflicts still present, out of 14 | 12 | 0 |
| New conflicts with an existing answer | 4 | 4 |
| Optimal shipment choices, out of 96 | 43 | 42 |

The specific review left 0.75 fewer conflicts per draft. It improved 6 worlds, tied 2 and worsened none (one-sided exact sign test, p = 1/64). But it got 1 fewer optimal choice out of 96. The registered test required no loss in this score, so it **did not pass**. A negative answer excludes a bay. It does not identify the best eligible alternative.

The [public receipt](results/constraint-review-live.json) contains all 32 response hashes, source references, scores and token charges. They total **$0.002159988** at the published rates: 4,096 cached input tokens, 9,966 uncached input tokens and 1,088 output tokens. No unresolved charge remains. This is a token-priced cost, not an invoice.

Read the public receipt without a key, Docker or a package install:

```sh
python3 -S examples/seatlab/constraint_review/analysis.py
```

## Can a dispatcher help another agent decide which two reports to send?

Two live agents share a strict message budget. The dispatcher writes 6 preferred bay letters. The inventory agent chooses 2 revised reports to send. Both policies generate a card. Only `brief` gives it to inventory. The final dispatcher receives the selected reports, not the card. The [register](replication.md#can-a-six-letter-brief-help-another-agent-prioritize-revisions) fixes the order and the 6 worlds before either model runs.

**DeepSeek Flash:** All 24 native teams finished. Releasing the card increased the native reward by **5.56 percentage points**: 64 versus 60 optimal choices out of 72 per arm. The sender selected 24 changed reports out of 24 slots in each arm. Of these, 14 versus 13 affected the best bay. The generated cards named the dispatcher's actual top bay in 50/72 released slots and 53/72 masked slots. The card did not always encode the correct preference. This comparison does not isolate which card errors caused later decisions.

The [direct report](results/briefing-direct-live.json) contains every accepted action and all 96 turn hashes. The [independent stock reader](results/briefing-direct-stock.json) matched every native reward and all 144 dispatch decisions. The [direct ledger](results/briefing-direct-cost.json) estimates **$0.007830252 USD** for all 96 calls, including the [4 pilot calls](results/briefing-direct-pilot-cost.json) at $0.000348450. No call has an unsettled reservation. These are token-price estimates, not invoice amounts.

**Keyless subscription:** The [second cohort](results/briefing-subscription-live.json) finished all 24 teams. The released cards named the true preferred bay in 72/72 slots. The masked cards did too. Releasing the card scored **1.39 percentage points lower**: 64 versus 65 optimal decisions out of 72. The sender selected 24 changed reports in each arm, including 10 versus 11 that affected the best bay. An accurate preference card was not sufficient to improve the score here.

The [stock check](results/briefing-subscription-stock.json) matched all 144 subscription decisions. The [external receipts](results/briefing-subscription-receipts.json) match 48 inventory and 48 dispatch calls to `openai-codex/gpt-6.2-sol`. Native records do not supply their token counts. There were no paid API calls in this cohort; subscription cost is not measured. The two cohorts used the **same 6 worlds**. Neither the sign difference nor this small number of worlds establishes a general ranking.

## Can a native dispatcher use revision notices to allocate two reads?

The [native attention experiment](replication.md#can-a-change-notice-help-a-live-team-spend-two-reads) starts an inventory agent and a dispatch agent in each native trial. Both arms may open 2 of 6 current reports. The `named` arm also tells the dispatcher which of the sender's reports changed. The first named team earned 0.5 native reward. Its 2 reads covered changed reports. One changed the optimal bay. All 3 direct calls settled at **$0.000315300 USD**. This first team does not measure the treatment difference.

**Direct model: naming the revisions improved the native reward by 15.28 percentage points.** All 24 teams completed. The named dispatchers opened changed reports on 24 of 24 reads. Count-only dispatchers did so on 12 of 24. The named arm opened 17 reports that changed the best bay, versus 9. It made 49/72 optimal decisions, versus 38/72. Feasibility differed by only one decision: 54/72 versus 53/72. Neither group had a sender transcription error. Three worlds improved, 2 tied, and 1 declined.

The [first-attempt report](results/attention-direct-live.json) contains all 12 matched pairs. [Stock Eval Lab](results/attention-direct-stock.json) agreed on every reward and all 144 native decisions. [All 72 direct turns](results/attention-direct-cost.json) matched the host ledger at **$0.006936288 USD**, including the first team. No hold remains. These 6 synthetic worlds do not establish a benefit for arbitrary tasks.

**Subscription replication: the same change improved native reward by 12.50 percentage points.** The named dispatchers opened 24/24 changed reports, versus 8/24. They made 63/72 optimal choices, versus 54/72. They made 66/72 feasible choices, versus 62/72. Four worlds improved and 2 tied. Both inventory groups copied every report correctly. This is the same set of 6 worlds, not 6 new independent observations.

The [subscription panel](results/attention-subscription-live.json) contains 24 fresh native teams, not rescores of direct-model teams. [Stock Eval Lab](results/attention-subscription-stock.json) agreed on all 144 actions and 24 rewards. The native results reported no model usage for command seats. Separate [service receipts](results/attention-subscription-receipts.json) matched 24 inventory and 48 dispatch calls to `openai-codex/gpt-6.2-sol`. No direct API call was made for this panel. We do not assign the subscription a cash price.

## Does a revised record help a replacement agent find the right context?

The [handoff experiment](replication.md#which-record-should-a-replacement-agent-read) shares one writer's note between two replacement policies. Both may read one current record. Only one policy gets the record name on each revision receipt.

The first DeepSeek writer returned a 337-byte note against the 256-byte limit. It returned valid JSON, but neither replacement received a note. The host ledger matched the one call at **$0.000072300 USD**. No decision or native score exists for it. The experiment retains this block and does not retry the writer.

Two independent local programs also ran a non-shipping example with 18 subprocess calls. The replacement answered questions about opening hours. Two changed cases used separate final answers, and two unchanged cases shared one actual answer each. No private note or opening time appeared in the report. This checks the portable interface, not a model.

**Naming the revised record improved lookup, but many final answers were invalid.** On changed cases, the named policy selected the revised record on **77 of 77** valid selections. The anonymous policy selected it on **17 of 70**. The changed record had an optimal final choice in 39 of 50 observed named decisions and 20 of 45 anonymous decisions. These last two denominators differ.

The [complete decision record](results/handoff-live.json) has all 120 claimed blocks. Of the 96 changed blocks, both decisions were valid in only **38**. Across those pairs, the named policy improved 13, worsened 1, and tied 24. The registered equal-world difference was **+32.64 percentage points** on complete pairs. Across all 96 scheduled comparisons, the missing-data bounds are **−27.08 to +61.46 points**. These are not confidence intervals. The study does not establish an improvement across the full register. The 20 complete stable controls tied.

The [stock check](results/handoff-stock.json) confirmed 416 choices in 104 distinct final decisions across 12 worlds. Both policies shared 31 complete decisions after opening the same record. All [470 first calls](results/handoff-cost.json) matched the host ledger at **$0.025455102 USD** including the first block. The final ledger has no open hold. No new Harbor trial ran for this comparison.

The [96-byte follow-up](replication.md#can-a-model-pack-an-actual-outgoing-note-better-than-a-byte-prefix) preserves all 120 actual writer responses, including the 21 that failed the first study's note contract. The [source seal](results/packing-source.json) pins them without publishing the notes. As a separate interface check, two desk programs compared clipping with extracting the hours from four 166-byte notes. All 12 packing and decision processes completed at no cost. This checks the harness only.

The first packing block reused the original 337-byte note. Each policy produced a complete new decision. All 3 model calls matched the ledger at **$0.000180150 USD**. The original block still has no decision.

Across [all 120 notes](results/packing-live.json), the rewrite and the byte prefix produced identical text 27 times. Among the 66 blocks with two complete decisions, rewriting improved unchanged-record accuracy in 16 blocks, reduced it in 6, and tied in 44. The equal-world difference was **+9.25 percentage points** among complete pairs. Bounds across all 120 blocks span **−24.03 to +37.64 points**. Rewriting required one extra model call per note. This result does not justify a general switch from clipping to rewriting.

The [stock check](results/packing-stock.json) confirmed 592 choices in 148 distinct decisions. The [ledger](results/packing-cost.json) matched all 333 calls at **$0.018413250 USD**, including the first block. No hold remains.

An offline inspection also identified why the original final answers were incomplete. Of 152 distinct received final answers, 104 met the contract. All other **48** returned a well-formed `choices` object for exactly one registered ID: the record the agent just opened. Each returned a valid option for that ID. The contract required all 4 IDs. We did not infer the other choices or change any score.

The [output ownership follow-up](replication.md#does-assigning-answer-ownership-prevent-incomplete-handoffs) froze 99 actual final prompts before collecting new decisions. In the first eligible block, the batch, checklist, and 4 independent answer owners all submitted complete choices. Stock confirmed all 12 choices. All 6 direct calls matched at **$0.000343434 USD**. This single block does not distinguish the strategies.

**Listing every required ID improved delivery without adding a model call.** We replayed the same 99 saved requests under each policy. The other 21 of 120 registered blocks had no valid writer note and no receiver request. No receiver failure was retried.

| Policy | Complete answers / 99 requests | Correct choices / 396 requested | Direct calls | Estimated USD |
| --- | ---: | ---: | ---: | ---: |
| Original batch instruction | 66 | 219 | 99 | $0.004326366 |
| Explicit ID checklist | 99 | 321 | 99 | $0.007002900 |
| One reader per record | 55 | 304 | 396 | $0.023193600 |

The checklist completed 33 answers that the batch instruction missed and lost none. The equal-world delivery difference was **+32.29 percentage points** across 12 worlds. Giving 4 independent readers one record each reduced complete delivery by **44.45 points** against the checklist. Partial owner answers still count toward correct choices. Among complete answers alone, batch accuracy was 219/264 and checklist accuracy was 321/396. The result supports a delivery contract, not higher accuracy on every answer.

The [first-attempt decisions](results/coverage-live.json) retain all 594 received calls. The [stock check](results/coverage-stock.json) verified 1,003 submitted choices with no score differences. The [cost ledger](results/coverage-cost.json) matched every call at **$0.034522866 USD**, including the pilot. No hold remains. This compares new receivers on saved prompts; it is not a change in a native team reward.

The [saved-trial replication](replication.md#repeat-the-handoff-comparison-on-saved-native-harbor-trials) uses the accepted sender messages from 3 actual native teams. All 6 source seats acknowledged their input. Both replacement policies in the first block chose the same record and shared a complete new decision. All 4 direct calls matched at **$0.000276468 USD**. The saved Harbor scores did not change.

The first registered subscription block used the same saved native packet. Its writer produced a valid note. Both policies opened the same record and shared a complete decision. All 4 service responses matched the writer or successor journal by both text hashes. The gateway reported `openai-codex/gpt-6.2-sol`, 1,493 input tokens, 148 output tokens, and 0 cached tokens. This measures a host-side replay, not a new Harbor reward or a cash price.

All [12 registered subscription handoffs](results/handoff-subscription-live.json) produced a valid note and a complete decision under both receipt policies. Both policies opened `S0` on all 12 blocks. Every block therefore shared its final decision. The [native scorer](results/handoff-subscription-stock.json) confirmed **72 correct choices out of 72** across 12 distinct decisions. On the 6 changed blocks, `S0` was the revised record by design. None of those revisions changed the best bay. This cohort verifies that another harness works; it cannot measure a benefit from naming revisions or rank the models.

The [role audit](results/handoff-subscription-usage.json) matched all 12 writer responses and all 36 successor responses to the requested subscription model. It found 17,841 input tokens, 1,791 output tokens, and no cached tokens. The other 30 blocks in the generic 42-block report were not registered for this test. They are not failed teams.

On the [42 recorded-source handoffs](results/handoff-recorded-live.json), naming the record selected the revised record **19/19** times, versus **4/19** without names. Only 8 of 36 changed blocks had two complete final decisions. The named policy improved 2, worsened 0, and tied 6. The equal-world difference was **+19.44 points** among complete pairs. The full missing-data bounds span **−47.22 to +75.00 points**. No stable control yielded two valid final decisions. This supports the lookup observation, not a general dispatch-score gain.

Half the new writers returned an invalid note: 21 of 42. The [native stock check](results/handoff-recorded-stock.json) confirmed 132 observed choices in 22 distinct final decisions. They came from all 3 eligible source worlds. The [host ledger](results/handoff-recorded-cost.json) matched all 120 calls at **$0.007863198 USD**, including the first block. No hold remains.

## Can a new dispatcher learn which stock source to trust?

The [registered experiment](replication.md#how-quickly-does-a-team-stop-trusting-a-stale-source) gives the selector only feedback from its own team's completed decisions. One policy remembers all of them. The other remembers the last 2. A separate inventory agent reports the stock it sees. Half the source assignments reverse without notice.

The first registered block produced 18 decisions. All 52 direct DeepSeek calls matched the host ledger at **$0.000924900 USD**, with no open hold. An independent stock check agreed on all 18 decisions. Only 1 of 12 native worlds had observations at this point. There is no full-study estimate from this block.

The [two-program command](replication.md#run-different-agent-programs-on-the-same-stock-changes) also ran with separate, non-model programs on a custom seed. It produced 72 decisions over 4 blocks, with no differences from the stock scorer. This checks the interface. It is not model evidence.

## What happens when a second agent sees different evidence?

**The complementary reviewer helped on the clean source and hurt on the noisiest one.** Both agents used direct DeepSeek Flash. The reviewer saw either the decision maker's own fields or a different pair of fields. The decision maker received either the reviewer's vote alone or the same vote plus its transcribed numbers. Both delivery arms used the same actual review. These are new model decisions using stored observations, not new native rewards.

| Source | Duplicate: vote | Duplicate: vote + numbers | Complementary: vote | Complementary: vote + numbers |
| --- | ---: | ---: | ---: | ---: |
| Clean reports | 56/72 | 63/72 | 51/72 | 69/72 |
| Intermediate source | 49/71 | 55/71 | 50/71 | 51/71 |
| Noisiest source | 48/72 | 50/72 | 51/72 | 39/72 |

The registered effect of complementary rather than duplicate evidence was **−4.19 percentage points** across 215 complete matched blocks. It improved 32 blocks, worsened 41, and tied 142. The full 216-block range was **−4.63 to −3.70 points**, including unknown outcomes. These are missing-data bounds, not confidence intervals. Adding the numbers rather than only the vote improved accuracy by 3.25 points for complementary views and 6.98 points for duplicate views.

Every measured reviewer copied its own numbers exactly: **431/431**. Accurate copying did not make an incorrect source report true. The [numeric reference](results/crucible-routed-reference.json) used each physical case and coordinator view once. Compared with using only the coordinator's two fields, averaging overlapping values from both views fixed 0 decisions and worsened 9 of 54. This describes this packet, not arbitrary information sharing.

The 3 archived source trials contain 18 source-rounds but only **6 distinct true worlds** and 12 distinct combinations of reports and truth. A/B swaps and repeated generations are not additional worlds. The [full decisions and prompt hashes](results/crucible-routed-live.json) include all 216 first-attempt claims. We did not retry the interrupted block. The [host meter](results/crucible-routed-cost.json) reconciled 1,291 calls at **$0.039034800 USD**. One unresolved request retains a $0.01 hold, for an upper estimate of **$0.049034800 USD**. Four other registered turns have no ledger row. These amounts are not invoices.

Use the [two-program CLI](replication.md#run-your-own-reviewer-and-decision-maker) to test other reviewer and decision programs against the same fixed packet.

## Does identifying a reviewer's track record improve delegation?

**Yes, in this packet.** The coordinator could ask only one of two reviewers. Both conditions showed the same two historical accuracy counts. Only the named condition said which reviewer earned each count. Neither condition showed the current answer. A separate live reviewer returned a recommendation and its numbers.

| Measure | Named history | Anonymous history |
| --- | ---: | ---: |
| Selected the historically more accurate reviewer when the counts differed | 57/64 | 28/64 |
| Selected a reviewer whose current numbers were all correct | 137/144 | 115/144 |
| Made the correct final decision | 129/143 | 107/143 |

The [registered comparison](replication.md#can-a-coordinator-use-team-memory-to-choose-one-reviewer) gives each of the 3 source policies equal weight. Naming the reviewer improved accuracy by **15.40 percentage points**. It improved 23 matched decisions, worsened 1, and tied 119. One interrupted block has no final decision in either condition. Across all 144 blocks, the missing-data bounds are **+14.58 to +15.97 points**. They are not confidence intervals.

The difference was 0 points on the clean source, +17.02 on the intermediate source, and +29.17 on the noisiest source. The 12 target source-rounds contain **4 distinct true worlds** and 8 distinct combinations of reports and truth. Repeating a call or swapping labels does not add a world.

The selectors agreed in 86 blocks. Those blocks shared one actual review and final answer, not two independent generations. The 286 scored policy decisions used 200 distinct final responses. The [decision record](results/crucible-delegation-live.json) preserves the interrupted claim. We continued only with untouched blocks. The [cost record](results/crucible-delegation-cost.json) contains 688 settled calls at **$0.022440900 USD** and no open hold. A callback without a ledger row does not prove that no network request occurred.

This tests supplied, correctly attributed history. It does not show that the original Harbor agents learned those histories, or that trust remains useful when reviewer quality changes.

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
