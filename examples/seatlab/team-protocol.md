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

Run the following commands from the fork root. Use a project-local Harbor 0.26.0 installation. Use a separate private output directory and the [existing direct spend guard](replication.md#run-a-new-blinded-listener-experiment). `plan` and `analysis` run offline. `run` starts a native Docker trial and can charge for up to 8 direct calls.

```bash
export PYTHONPATH="$PWD/src:$PWD/examples/seatlab:$PWD/examples:$PWD"
python -m examples.seatlab.committee.driver plan
# TASK points to the exact unmodified hidden_profile_unshared task linked above.
keys run -- python -m examples.seatlab.committee.driver run \
  --out "$PRIVATE_STUDY" --harbor "$HARBOR" --task "$TASK" --cell hp-m0-r0-open
python -m examples.seatlab.committee.analysis "$PRIVATE_STUDY" --spend-dir "$SEATLAB_SPEND_DIR"
```

### Voting follow-up: does computing totals before choosing help?

**Registered before the first follow-up call.** The previous teams supplied all 4 private facts in 11 trials without a single winning majority. This study changes the voting interface, not the sender instruction. No tool computes the totals for the model, chooses its vote, repairs arithmetic, or changes the native verifier.

Use the same unmodified task and direct model. Each seat in every new trial uses the original **novelty** sharing prompt, then votes on the 4 actual posts. Run 3 voter interfaces:

- `plain`: use the original vote prompt and response format without changes.
- `first`: ask the voter to write its 3 signed integer totals **before** its choice and citations.
- `last`: ask it to write its choice and citations **before** the same 3 totals.

Require each structured response to contain 3 labeled integer totals, 1 valid choice, and valid visible citations. Accept incorrect totals and choices that disagree with the model's own totals. Submit only the model's choice and citations to the stock world. Record the actual response field order without forcing it to match the requested order. Plain responses have no observed totals.

Use each of the 6 label maps twice per interface: 36 new teams and at most 288 direct requests. For map `m` and repetition `r`, rotate `[plain, first, last]` left by `(m + r) mod 3`. Reverse the rotated order if `r = 1`. Use the same direct spend directory and limits. Keep each first attempt. Continue past a rejected model action only if the native evidence identifies that failure, any other failures are waiting on a missing peer, and all attempted calls are settled. Stop for a provider, budget, transport, evidence, or environment failure. Never retry a failed trial.

Primary: `first − plain` in native team reward on complete pairs. Average repeats within map; weight all 6 maps equally. Secondary: `first − last`, correct individual votes, per-seat agreement between reported totals and the signed sum of *visible distinct* facts, agreement between the vote and the model's own largest reported total, actual response field order, and output tokens. Show differences in submitted messages across arms. Separately report matched comparisons where every analyst has the same observable evidence. Do not call unmatched team contexts identical. An incomplete arm never counts as a wrong vote. This is still 1 vendor problem.