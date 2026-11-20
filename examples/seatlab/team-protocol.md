# A controlled native speaker–listener experiment

**Question:** When an inventory report and a dispatch decision are separate real actions, does the speaker or listener determine the final choice? This extension uses 2 under-test participants in each native Harbor trial. It is not the earlier provider replay and does not replace any missing primary result.

## Fixed design

Use the existing dispatch world and trusted independent verifier. Use 2 new seeds, `2001` and `2003`, and 12 shipments per seed. Compare `fresh` and `reordered`. For each world run 4 pairings: script/script (`SS`), script/model (`SM`), model/script (`MS`), model/model (`MM`), where the first letter means inventory. Scripts use the existing arrival-order participant, not the revision-aware oracle. Every model participant uses exactly one restricted parent-hosted first-party Claude CLI generation with only its own accepted native world view. Inventory model output is interpreted as its actual two reports; it may be wrong. Dispatch sees the actual delivered reports and determines the final bay. No correction using the hidden reference is permitted.

Plan 16 distinct native trials and at most 16 model calls. Keep seeds and conditions paired; alternate condition order by seed and reverse the pairing order in the second seed. Use only the existing Claude Max subscription, with a $0.30 CLI provider-estimate bound per call ($4.80 across the experiment). Do not use an API key, a paid fallback, model retry, tools, MCP, project settings or an alternative model. Stop opening new cells on a failed preflight, malformed model result, budget violation, refusal, rate limit, or missing native grade. Report all unattempted cells and incurred known estimates. A cost estimate is not an invoice. There is no charge for the local Docker instances.

## Acceptance

Register the whole grid, actor source hashes, and first-party model identity before launch. For every completed cell verify 2 native seat records, 2 accepted role-view commitments, actual reports, delivered reports, native dispatch choices, the independent native score and both actor identities. Check the served model from the returned CLI metadata, not only the desired config. For scripted seats require zero model turns. For a model seat require exactly one confirmed turn. Unavailable usage is `null`, not zero. Native synthetic evidence can support comparing the reports sent with the reports that should have been sent. Do not interpret an acknowledged input as proof of understanding.

Primary endpoint: paired changes in native choices and optimal counts when substituting only the listener, conditional on equal actual delivered messages. Report upstream message changes separately instead of attributing those to a listener. Secondary: both revision reports vs the speaker's own native input, feasible choices, per-seat reported usage and actual number of complete trials. A scalar reward alone cannot verify these contrasts. With 2 seeds, do not infer population effects or rank models. No original primary, held-out, or calendar trials count toward these denominators.

## Does one sentence help 4 analysts pool private information?

This is a separate, prospective experiment on the [original public Crucible task](https://github.com/PeterMakhnatch/harbor-crucible/tree/25966562fc4be3fbc8a8c3b10d8460bd45c4aa55/tasks/epistemic-crucible/variants/hidden_profile_unshared). Keep the task, world, instructions, network, and native verifier unchanged. Its Git tree is `62e6d77d171b2c022f6857781ad3ab9e4e0d4c7d`. Its Harbor task checksum is `adb41137272dabea3eb946c8733da5424d77520200f373b14f7ef08913da4e1d`.

Each analyst has 5 shared facts and 1 private fact. The shared facts favor the wrong vendor by 20 points. Each private fact reduces this gap by 9 points. Thus, 3 distinct private facts in the public thread suffice to reverse the ranking. Individual analysts can also use their own private fact when they vote. **The intervention changes the speaker instruction, not the available facts or the voting rule.**

- Each of 4 native seats uses the direct `deepseek-flash` model twice. First it chooses exactly 1 fact from its dossier to publish. Then it sees all 4 actual submissions and chooses a vendor. The adapter does not select facts, compute the vote, correct a model, or use the answer key. It does not send task prose, seat tokens, native vendor IDs, or hidden facts to a model.
- Both arms show the same private and shared numerical facts. Only the `novelty` share prompt adds: “Prefer a fact the other analysts cannot already see.” The `open` prompt has no such sentence. All seats see the same voting instruction. They count a factual claim once, even if multiple analysts chose it.
- Map the 3 native options to `X`, `Y`, and `Z` using all 6 permutations in lexicographic order. Within each map, assign all 9 fact IDs neutral aliases by sorting the SHA-256 of `seatlab-committee-v1:{map_index}:{native_id}`. Keep each mapping identical across arms and repeats. Sort messages independently of arrival time for the voting prompt.
- Run 2 independent repetitions per map and arm, for 24 native trials and at most 192 new model requests. Visit map index `0..5`, then repetition `0..1`. Use `open` first when their sum is even and `novelty` first otherwise. Run each cell once. Retain a missing or failed cell; do not substitute another.
- Primary outcome: number of distinct, authenticated, correctly attributed private facts posted, from 0 to 4. Compare `novelty − open` within a complete pair. Average repeated contrasts within each map and then weight the 6 maps equally. Report available pair counts, all map contrasts, and no 6-map primary estimate if a map lacks a complete pair.
- Secondary outcomes: fraction of trials reaching 3 private facts, individual correct votes, and native team reward. The stock verifier requires at least 3 correct votes for a win. Report any disagreement with the independently reconstructed strict majority. Verify the authenticated chat, model action, actual votes, and native verifier separately. Do not treat a missing vote as a loss.
- Keep all 8 turns' actual served models, token-based costs, and unresolved reservations. Use the same temperature, JSON-only transport, account balance floor, and cumulative $1 local cap as the [direct Flash protocol](replication.md#direct-deepseek-cohort-amendment-before-its-first-call). This experiment does not reset the allowance. Stop on an accounting guard or a systematic platform failure. Do not retry failed generations.

The 6 label maps repeat **one underlying team problem**. They are not 6 independent tasks. The fixed one-message budget tests information selection; it does not measure whether agents would speak voluntarily in an open conversation.

### What the 4 analysts did

**The novelty cue changed what analysts shared. It did not produce a winning majority.** Both arms used the same voting instruction. Each vote used the analyst's own dossier and the 4 real posts.

| Measured outcome | Open instruction | Novelty instruction |
|---|---:|---:|
| Complete native teams | 11 | 11 |
| Private facts in 44 posts | 5 | 44 |
| Teams that published at least 3 distinct private facts | 0 | 11 |
| Correct individual votes | 3 / 44 | 14 / 44 |
| Teams with a correct majority | 1 / 11 | 0 / 11 |

Within matched trials, the cue added **3.58 distinct private facts per team** after weighting each label map equally. The cue added 0.92 correct votes per team with the same weighting. This was not enough to produce a majority in any novelty trial. The one open team that won posted no private facts. A winning vote alone does not establish successful evidence sharing. These observations concern **one vendor problem and one model**, not all team tasks.

We scheduled 24 trials and completed 11 pairs across all 6 label maps. In trial `hp-m5-r1-open`, 1 analyst returned an invalid share action. The other 3 submitted a fact and timed out waiting for the fourth. No vote requests ran. The driver stopped before `hp-m5-r1-novelty`. Neither cell counted as a loss. We did not retry either cell.

The [public per-seat results](results/committee-sharing.json) include all 24 slots and the actual submitted votes and citations for the 22 complete trials. The [cost report](results/committee-sharing-cost.json) covers all 180 direct requests, including the 4 paid share requests in the incomplete trial. The list-price estimate is **$0.007414092**: 11,264 cached input tokens, 40,370 uncached input tokens, and 2,208 output tokens. No reservation remains open. This estimate is not an invoice.

Stock Eval Lab read 23 native result files. It matched all 22 accepted team rewards, including the zeros. Native token counts matched the 2 provider turns for 88 seats. They also matched the single paid turn for each of the 4 seats in the failed trial. The unattempted slot had no result. The [accounting comparison](results/committee-sharing-accounting.json) does not copy provider values into missing native fields. It uses only `evallab.results.load_trial` and needs no database.

Run the following commands from the fork root. Use a project-local Harbor 0.26.0 installation. Use a separate private output directory and the [existing direct spend guard](replication.md#run-a-new-blinded-listener-experiment). `plan` and `analysis` run offline. `run` starts a native Docker trial and can charge for up to 8 direct calls.

```bash
export PYTHONPATH="$PWD/src:$PWD/examples/seatlab:$PWD/examples:$PWD"
python -m examples.seatlab.committee.driver plan
# TASK points to the exact unmodified hidden_profile_unshared task linked above.
keys run -- python -m examples.seatlab.committee.driver run \
  --out "$PRIVATE_STUDY" --harbor "$HARBOR" --task "$TASK" --cell hp-m0-r0-open
python -m examples.seatlab.committee.analysis "$PRIVATE_STUDY" --spend-dir "$SEATLAB_SPEND_DIR"
# Read-only, with the Python environment that already contains Eval Lab:
"$EVAL_LAB/.venv/bin/python" -B -m examples.seatlab.committee.accounting \
  --study "$PRIVATE_STUDY" --spend-dir "$SEATLAB_SPEND_DIR"
```

### Live recommendation: should 3 peers receive a designated analyst's advice?

**Registered before the first recommendation trial.** These are fresh interactive Harbor teams, not second votes on frozen ballots. Test the delivery of advice without giving the team more model calls.

1. Each of 4 model seats shares 1 real dossier fact using the novelty instruction. Once all 4 posts arrive, each seat privately drafts a choice and citations. It sees no peer choices at this stage. The adapter posts that ballot to the native thread using blinded aliases.
2. A designated seat, the chair, sees the same evidence and all 4 draft ballots. It makes 1 new model call to recommend a choice and citations. It can recommend any visible choice, including a wrong one. No code computes a recommendation.
3. All 4 seats wait for the real chair post. Each final voter sees its own evidence and the 4 actual drafts. `shown` also shows the chair recommendation to every voter. `masked` shows it only to the chair. Both arms write identical *types* of posts, but independent models can submit different content. Each seat submits exactly 1 stock vote. A draft ballot never counts as a native vote.

Use all 6 label maps, 2 repetitions per map and 2 delivery arms. For map `m` and repetition `r`, chair seat index is `(2*m+r) mod 4`. Each seat chairs 3 matched pairs. Start `masked` when `m+r` is even; otherwise start `shown`. Use the same stock task, model, direct ledger and existing limits. A complete trial takes 13 direct calls. Schedule 24 first attempts and at most 312 calls. Stop the cohort on the first incomplete trial. Keep its charges and unattempted slots. Do not retry or replace it.

Primary: native team reward, `shown − masked`, paired within map and repetition. Average pairs within map, then weight the 6 maps equally. With no pair in any map, report a partial result, not a 6-map estimate. Secondary: correct votes, changes towards the chair among 3 nonchair voters, and changes from wrong to correct or correct to wrong. Count only actual submitted choices. Report draft disagreement, differences in facts and first ballots across paired teams, and the chair's own accuracy. Never infer that a model used advice solely because its vote agreed. The 6 maps repeat one vendor problem.

The stock server returns the whole thread. The host selects which messages reach each model. Bind each prompt to its source board snapshot, native action and seat receipt. The control masks the chair **from 3 model requests**, not from the native server. The 2 arms use the same number of posts, ballots, and calls when complete.

#### What changed when peers received the chair's recommendation?

All 24 native teams finished. Each made 13 direct calls. Showing advice did **not** change the primary outcome: neither arm produced a correct native majority in any of the 12 pairs.

| Observed in Harbor | Advice masked from peers | Advice shown to peers |
| --- | ---: | ---: |
| Teams with a correct final majority | 0 / 12 | 0 / 12 |
| Correct chair recommendations | 1 / 12 | 0 / 12 |
| Correct draft votes | 10 / 48 | 11 / 48 |
| Correct final votes | 8 / 48 | 0 / 48 |
| Initially dissenting non-chair voters who switched to the chair's choice | 2 / 9 | 11 / 11 |
| Non-chair voters who changed from correct to wrong | 2 / 36 | 11 / 36 |

The visible recommendations were all wrong. All 11 initially dissenting peers switched to the recommended choice. This does not isolate the effect of an identical recommendation: all 12 pairs differed in at least one draft ballot or citation. The recommended choice or citations differed in 5 pairs. The model and the underlying vendor problem were the same throughout.

The [native results](results/committee-recommendation-results.json) include each team and paired contrast. The [312 settled calls](results/committee-recommendation-cost.json) used 43,008 cached input tokens, 71,379 uncached input tokens, and 4,872 output tokens. The price estimate is **$0.013759074 USD**. Eval Lab's read-only `load_trial` matched all 24 native rewards and all 96 seat token records. No database write was needed.

#### Can we change advice visibility after the same native history?

The live study changed advice, but the paired teams did not always generate the same advice. This follow-up holds each source team's actual advice fixed. We know the live results. We register this continuation before its first model call.

Select repetition 0 of the `masked` source in each of the 6 maps. Do not select by recommendation, correctness, or disagreement. Admit only a source accepted by the native report and direct ledger. Cut after the ninth accepted chat post, before any vote. Keep each ordered dossier read, chat read, and chat post. Reject a prefix that cannot be reproduced in serial order. Do not replace it.

Start a new stock Harbor world for each continuation. Replay the accepted prefix through the original participants' ordinary APIs, without a model call. Verify every response that the source records. For dossiers, compare the structured facts, not unrecorded original descriptions. Require the same ordered world events through the cut, except for their wall-clock timestamps. Require 9 chat messages, 0 votes, and no completed seats before any new decision. This is not a container snapshot. It does not restore provider state, private model memory, or the original clock.

In both arms all 4 voters make fresh model calls and submit new votes to the native verifier. The chair receives the same source recommendation in both arms. `shown` delivers it to the other 3 voters; `masked` does not. Do not tell the model the arm, original final votes, or original outcome. Run 2 repetitions per eligible map. Start `masked` when the map index plus repetition is even. Plan at most 24 native teams and 96 direct calls. Never retry a paid sample. Stop for an incomplete continuation, a prefix mismatch, or an accounting failure. Preserve missing cells and settled charges.

Primary: the mean paired change in native majority reward, `shown − masked`. Average the repetitions within each source, then give each source equal weight. Require all 6 sources to give a 6-source estimate. Secondary: non-chair voters switching to the chair among those who originally disagreed, final correct votes, and chair variation with identical input. Report the count of exact prefix matches. Never substitute a reconstructed majority for the native reward. The 6 source histories still come from 1 vendor problem.

### Voting follow-up: does computing totals before choosing help?

**Registered before the first follow-up call.** The previous teams supplied all 4 private facts in 11 trials without a single winning majority. This study changes the voting interface, not the sender instruction. No tool computes the totals for the model, chooses its vote, repairs arithmetic, or changes the native verifier.

Use the same unmodified task and direct model. Each seat in every new trial uses the original **novelty** sharing prompt, then votes on the 4 actual posts. Run 3 voter interfaces:

- `plain`: use the original vote prompt and response format without changes.
- `first`: ask the voter to write its 3 signed integer totals **before** its choice and citations.
- `last`: ask it to write its choice and citations **before** the same 3 totals.

Require each structured response to contain 3 labeled integer totals, 1 valid choice, and valid visible citations. Accept incorrect totals and choices that disagree with the model's own totals. Submit only the model's choice and citations to the stock world. Record the actual response field order without forcing it to match the requested order. Plain responses have no observed totals.

Use each of the 6 label maps twice per interface: 36 new teams and at most 288 direct requests. For map `m` and repetition `r`, rotate `[plain, first, last]` left by `(m + r) mod 3`. Reverse the rotated order if `r = 1`. Use the same direct spend directory and limits. Keep each first attempt. Continue past a rejected model action only if the native evidence identifies that failure, any other failures are waiting on a missing peer, and all attempted calls are settled. Stop for a provider, budget, transport, evidence, or environment failure. Never retry a failed trial.

Primary: `first − plain` in native team reward on complete pairs. Average repeats within map; weight all 6 maps equally. Secondary: `first − last`, correct individual votes, per-seat agreement between reported totals and the signed sum of *visible distinct* facts, agreement between the vote and the model's own largest reported total, actual response field order, and output tokens. Show differences in submitted messages across arms. Separately report matched comparisons where every analyst has the same observable evidence. Do not call unmatched team contexts identical. An incomplete arm never counts as a wrong vote. This is still 1 vendor problem.

#### What changed when analysts wrote totals first?

All 36 native teams completed. Every team shared all 4 private facts. The paired teams had the same observable facts in every registered comparison.

| Outcome | Plain ballot | Totals before choice | Totals after choice |
| --- | ---: | ---: | ---: |
| Teams with a correct majority | 0 / 12 | 11 / 12 | 0 / 12 |
| Correct individual votes | 10 / 48 | 45 / 48 | 11 / 48 |
| All 3 reported totals exactly correct | Not requested | 0 / 48 | 5 / 48 |
| Choice matched the highest reported total | Not requested | 48 / 48 | 23 / 48 |
| Output tokens on voting turns | 963 | 1,697 | 1,761 |

**Writing totals first changed the vote, but did not make the totals correct.** The primary increase in native majority wins was 91.7 percentage points. The increase was also 91.7 points against the totals-last interface. All 96 structured votes followed the requested field order. The host accepted wrong totals and never used them to choose or repair a vote. The results concern 1 model on 1 vendor problem. Do not treat an accurate vote as evidence of accurate arithmetic.

The [native results](results/committee-tally-results.json) include both paired comparisons and each accepted readout. The [288 direct calls](results/committee-tally-cost.json) used 18,432 cached input tokens, 68,736 uncached input tokens, and 5,285 output tokens. The price estimate is **$0.013536696 USD**, not an invoice. No reservation remains open.

### Peer ballots: does showing the other 3 votes help a second decision?

**Registered after the sharing study, before any second-ballot requests.** The source is the first `novelty` repetition for **every** label map. All 6 source teams completed. We know their original votes. We do not select teams by disagreement or outcome. This experiment changes a recorded team's information policy; it does not run those native teams again.

Freeze the original 4 submitted facts and 4 first ballots in each team. Bind every reconstructed first-vote prompt and submitted action to its native seat receipt and world event. Publish only blinded aliases, source commitments, and the structured facts and ballots. An independent receiver gets its original evidence and its own actual first ballot. It does not get the true answer, other private dossiers, or the original team score.

Use 3 second-ballot interfaces. Each makes 1 new direct request per seat. `private` hides the other 3 ballots. `choices` shows their 3 choices only. `cited` shows the same choices and the citations those peers actually submitted. Keep the original evidence, own ballot, seat order, and final response format constant. Do not synthesize a debate. These voters cannot respond to each other's new answers.

Use 2 complete blocks: 6 source teams × 4 seats × 3 interfaces × 2 blocks = 144 planned requests. Rotate the interface order by `(map_index + seat_index + block) mod 3`. Reverse that order in block 1. Never retry a request or repair a vote. Keep a missing receipt distinct from a wrong answer. Use the same direct spend limit. Stop on a transport, identity, or budget failure.

Primary: `choices − private` in the fraction of correct second votes. Secondary: `cited − choices`, strict group majority (3 of 4), changes towards a unique plurality among the 3 peers, changes away from an initially correct vote, and cost. Report all denominators. Pair by original team, seat, and block. Average seat pairs and blocks within each source team; weight the 6 teams equally. A group comparison requires all 4 second votes in both interfaces. This is 1 vendor problem, not 6 independent tasks. A second-ballot majority is a **derived** result and must never replace the native reward.

#### What changed when analysts saw their peers' votes?

All 144 new model decisions completed. The 3 interfaces used the same 24 source seats, each sampled twice. The original 6 Harbor results did not change.

| New decision | Other ballots hidden | Peer choices | Peer choices and citations |
| --- | ---: | ---: | ---: |
| Correct second votes | 12 / 48 | 11 / 48 | 8 / 48 |
| Initially correct votes changed to wrong | 4 / 16 | 5 / 16 | 8 / 16 |
| Initially dissenting voters changed to the peers' plurality | 4 / 28 | 5 / 28 | 8 / 28 |
| Groups with a strict majority | 8 / 12 | 8 / 12 | 11 / 12 |
| Groups with a correct strict majority | 0 / 12 | 0 / 12 | 0 / 12 |

Showing choices changed the correct-vote rate by −2.1 percentage points against hiding them. Adding citations changed it by −6.25 points against showing choices alone. All 6 maps contributed to both comparisons. More groups reached agreement when the model saw citations. None reached a correct majority. Even the hidden interface changed 4 originally correct answers without receiving a peer ballot. The groups are reconstructions, not live deliberations. The results apply to this packet and model.

The [replay results](results/committee-peer-ballots.json) retain both blocks and all paired denominators. The [144 settled calls](results/committee-peer-ballots-cost.json) used 36,480 cached input tokens, 27,240 uncached input tokens, and 2,979 output tokens. The price estimate is **$0.005982840 USD**. No reservation remains open.

#### Run your own second-ballot agent without the private archive

The [public packet](results/peer-ballot-packet.json) contains 6 native source teams and the first actions of 24 seats. `verify`, `prompt`, and `decode` run offline with Python 3.12 or later. They do not require Harbor, Eval Lab, Docker, or an API key.

```bash
module=examples.seatlab.committee.ballot_packet
packet=examples/seatlab/results/peer-ballot-packet.json
digest=c8f743895f9e2fbecdf8b58201100bdb602186672a482c5545549408c5857095
python -m "$module" verify "$packet" --expect-sha256 "$digest"
python -m "$module" prompt "$packet" --expect-sha256 "$digest" --map 0 --seat 1 --arm choices > user.txt
# Give your agent only user.txt, never the whole packet. Save its response in ballot.json.
python -m "$module" decode "$packet" --expect-sha256 "$digest" --map 0 --seat 1 < ballot.json
```

Use `private` to hide the other ballots or `cited` to show their submitted citations. `decode` accepts a choice and citations visible to that voter. It does not correct a wrong choice. To reproduce the exact bytes, run `export "$PRIVATE_STUDY" --spend-dir "$SEATLAB_SPEND_DIR"` with the original trial archive. The external digest binds the packet bytes. It does not independently authenticate the provider.