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

### Does an index of distinct facts help an external voter add the evidence?

This study transfers a native Harbor decision into Inspect AI. It runs new model decisions. It does not create a new Harbor team or change an original reward.

Freeze the 6 source teams and 24 seats in the [published packet](results/peer-ballot-packet.json). Its SHA-256 is `c8f743895f9e2fbecdf8b58201100bdb602186672a482c5545549408c5857095`. Use only the facts that the original seat could see before its first vote. Withhold every source ballot and the task answer.

Each Inspect sample requests signed option totals, then a choice and citations. `separate` uses the original totals-first prompt with a dossier and 4 submissions. `indexed` shows each distinct fact once. It preserves the option, signed points, private/shared provenance, dossier membership, and number of submitted copies. It does not add points or suggest a choice. This tests the *whole layout*, not deduplication alone.

Run 2 blocks × 6 option maps × 4 seats × 2 layouts = 96 one-turn samples. Alternate which layout goes first by the parity of `block + map + seat`, with all indices starting at 0. Start with `separate` for even parity. Use one fixed direct DeepSeek model and the existing budget guard. Do not use tools, generation caches, retries, or fallback models. Stop at a provider, identity, or budget failure. Keep invalid responses and missing samples separate.

Primary: compare the fraction with all 3 visible totals correct, `indexed − separate`. Pair the same seat, map, and block. Average matched pairs within each map, then give each map equal weight. Report how many maps and pairs contribute. Secondary: correct choice against the native world; agreement with a visible maximum; output field order; derived 3-of-4 majorities on complete paired groups; tokens and estimated cost. These 6 maps relabel 1 vendor problem. They are not 6 independent tasks. No result from this study establishes an improvement in native Harbor rewards.

#### What the external voters did

All 96 Inspect samples finished in their registered order. The public packet supplied 24 source seats. Each seat received both layouts in 2 blocks. The index did **not** improve exact arithmetic in this experiment.

| Result | Separate dossier and submissions | Index of distinct facts |
| --- | ---: | ---: |
| Voters with all 3 totals correct | 2 / 48 | 0 / 48 |
| Voters who chose the winning option | 45 / 48 | 42 / 48 |
| Voters who returned totals before the choice | 48 / 48 | 48 / 48 |
| Groups with a correct 3-of-4 majority | 11 / 12 | 11 / 12 |

The paired change in exact arithmetic was −4.17 percentage points across all 6 maps. Each block had 1 exact answer with separate submissions and 0 with the index. The model often chose the right vendor without giving the right totals. The index changed the layout and duplicate presentation together. These results do not isolate either change. No Harbor team ran again.

The [sample results](results/inspect-packing-results.json) bind each Inspect model event to its expected seat prompt. The [96 settled direct calls](results/inspect-packing-cost.json) cost an estimated **$0.006045000 USD**. The direct ledger matched every sample's seat, model and token counts. It does not authenticate the model's response text.

#### Run the Harbor packet in Inspect AI

This task lives on [Peter's fork, `experiments/seatlab-inspect-replay`](https://github.com/PeterMakhnatch/harbor-multiagent/tree/experiments/seatlab-inspect-replay/examples/seatlab). It does not require a change to upstream Harbor.

```bash
git clone --depth 1 --single-branch --branch experiments/seatlab-inspect-replay https://github.com/PeterMakhnatch/harbor-multiagent.git
cd harbor-multiagent
```

From the root of this fork, install Inspect in a separate Python 3.12 environment. You do not need Harbor, Eval Lab, Docker, or the private trials. The protocol library pin matters: Inspect 0.3.276 imports `acp.helpers`, which the 1.0.0 release removes.

```bash
work=$(mktemp -d)
python3.12 -m venv "$work/venv"
"$work/venv/bin/python" -m pip install 'inspect-ai==0.3.276' 'agent-client-protocol==0.12.1'
task=examples/seatlab/inspect_replay.py@seatlab_facts
"$work/venv/bin/inspect" eval "$task" --model mockllm/model --limit 1 --display none --log-dir "$work/smoke"
```

This command checks that Inspect can load and score 1 sample. It does not measure a model. To evaluate your own model, set `MODEL` to an Inspect model name. Check your provider's price before running the next command. Inspect's standard provider does not use the SeatLab spend guard.

```bash
"$work/venv/bin/inspect" eval "$task" --model "$MODEL" --epochs 1 --max-samples 1 \
  --max-retries 0 --retry-on-error=0 --log-dir "$work/model"
```

Keep the `.eval` file private. It contains prompts and model responses. The task gives the model only the selected seat's evidence. The scorer reads the sealed packet separately. It does not send the packet or hidden answer to the model.

To summarize a private log, pass its exact `.eval` path. The report checks the recorded inputs against the sealed packet. It reports missing samples separately from wrong answers.

```bash
packet=examples/seatlab/results/peer-ballot-packet.json
digest=c8f743895f9e2fbecdf8b58201100bdb602186672a482c5545549408c5857095
"$work/venv/bin/python" -m examples.seatlab.committee.inspect_report "$packet" "$LOG" \
  --expect-sha256 "$digest" > "$work/receiver-results.json"
```

The registered direct run uses `python -m examples.seatlab.committee.inspect_direct --log-dir "$NEW_PRIVATE_DIR"` instead of the generic Inspect model command. It requires an initialized `SEATLAB_SPEND_DIR` and a direct `DEEPSEEK_API_KEY`. It refuses to restart this cohort after a reservation. Pass `--spend-dir "$SEATLAB_SPEND_DIR"` to the report for this run. The ledger can confirm cell identity, model, and token counts. It does not authenticate the response text.