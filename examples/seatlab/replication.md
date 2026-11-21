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

## What should a second agent see and send?

**Registered before collection.** Two roles make an operations choice from 2 partially observed numeric routes. The reviewer and the decision maker can use different harnesses. Compare a second look at the same evidence with a second look at different evidence. Also compare a vote with a vote plus its supporting numbers. Use the same reviewer reply in both message branches.

- Select the first UUID in the existing [blinded listener](#receiver-visibility-stored-teams-and-fresh-model-decisions) order for each of the 3 registered Crucible scout policies. Try all 6 rounds in each source. Require the original authenticated receiver fetch of all 3 reports before its decision. Reconstruct the answer from overlapping private observations, not the original decision. Keep any ineligible round in the register. Do not replace it.
- In each round, index the 3 source reports in this order: `scout_3`, `scout_1`, `scout_2`. Rotate the decision maker's report through all 3 positions. In `duplicate`, the reviewer receives the same report. In `complementary`, the reviewer receives the next report cyclically. The latter has 1 overlapping attribute and 1 other attribute. Do not identify the source, scout, treatment, or answer in either seat's instructions.
- Use both assignments of the display labels A and B, and 2 independent repetitions. This gives up to 216 blocks. The reviewer sees 4 numbers for 2 named attributes. It recommends a route and transcribes the numbers. Do not repair an incorrect transcription. The decision maker sees its own 4 numbers and either the reviewer's recommendation alone (`vote`), or the recommendation and its attributed numbers (`evidence`). A malformed review starts neither child. A malformed decision has no accepted choice.
- Use independent stateless calls, even when inputs match. Six first attempts per complete block: 2 reviewers and 4 decisions. Balance which reviewer input runs first by the parity of the zero-based policy, round, rotation, label map and repetition indices. Reverse which message branch runs first on the second reviewer. Give each role the same direct Flash settings as in the listener cohort. Keep missing, invalid and interrupted first attempts. No retries or paid fallback.
- Primary: compare complementary minus duplicate reviewer context **with the evidence channel**, paired within each block. Report correct physical choices, complete-pair differences and worst/best differences over the full register. Average within each source policy and weight the 3 policies equally. Secondary: the same difference with votes only; evidence minus vote for each reviewer input; the paired interaction on complete quartets. Report transcription errors, recommendation following, label swaps and repeat disagreement. Do not treat repeated rounds, permutations or identical prompts as independent worlds.
- The public packet contains only allowlisted numeric values, event references and source commitments. Only the scorer can use the answer key. Role callbacks receive a prompt string, not a path or the packet. These are new 2-agent decisions from recorded observations, **not** a native Harbor rerun, a measurement of the archived scouts' model use, or a comparison with a single model using all the evidence.

Within each lens, reverse the channel order across the 2 repetitions. Start with `vote` when `(rotation + repetition + is_complementary)` is even. This balances channel order separately from which reviewer runs first.

### Run your own reviewer and decision maker

The packet and its SHA-256 commitment ship on this branch. These commands need Python 3.12 or later. The demo does not use a model, an API key, Docker, Eval Lab, or the source archive.

```bash
module=examples.seatlab.visibility.route_cli
private=$(python -c 'import tempfile; from pathlib import Path; print(Path(tempfile.mkdtemp()).resolve())')
python -m "$module" plan
python -m "$module" demo --study "$private" --start 0 --stop 1
python -m "$module" report --study "$private" > "$private/report.json"
```

For model seats, use `commands` instead of `demo` and use a new private study directory. Give each role its own executable in a JSON config:

```json
{
  "reviewer": {"argv": ["/absolute/path/to/my-reviewer"], "env": []},
  "decider": {"argv": ["/absolute/path/to/my-decider"], "env": []}
}
```

Run `python -m "$module" commands --config seats.json --study "$private" --start 0 --stop 1`. Each program reads one UTF-8 JSON line: `{"schema":"seatlab.seat-turn/v1","prompt":"..."}`. It writes one JSON line: `{"text":"..."}`. Return the model's first text unchanged. The runner starts a fresh process for every turn. Both decision branches receive the same actual review. Different workers may claim disjoint block ranges in the same private study. The runner refuses a second attempt at a claimed block.

Use only trusted executables. The runner starts each in an empty working directory and passes only the environment variables named for that role. This is not an operating-system sandbox. An executable can still use the network and read files available to its user. The offline report verifies the selected role inputs and observed replies. It makes no claim about model identity or billing for external commands. For Python callbacks, use `route_run.play_block(..., reviewer=my_reviewer, decider=my_decider)`; both callbacks take one prompt string.

### Continue a study without repeating a model call

Keep the original packet, private study, and spend directory. `pending` makes no requests. It lists untouched block indices and half-open `start`/`stop` ranges. A claimed block stays closed even when its last reply is missing.

```sh
module=examples.seatlab.visibility.route_cli
python -m "$module" pending --workflow review --study "$private"
python -m "$module" commands --workflow review --study "$private" \
  --config seats.json --start 0 --stop 20 --only-unclaimed
```

Give each worker a different range. If ranges overlap, the first claim owns the block. Other workers skip it. Rerunning a range with `--only-unclaimed` does not repair interrupted blocks. A block directory without a complete claim is occupied, not available.

For `direct`, give `pending` the original `--spend-dir "$SEATLAB_SPEND_DIR"`. Set that same absolute directory in the worker environment. A recorded reservation closes a block even if the study directory no longer contains the claim. A new spend directory cannot detect an old payment. External command costs are not recorded by this ledger.

On the 2 published studies, all 216 review blocks and all 144 delegation blocks were closed. In empty replacement directories, the original ledger still closed every block. An independent local transport check used 2 overlapping workers, 1 interrupted block, and 1 unbound claim. It reached 38 stages once each. A second pass made no calls and did not change the evidence. The transport check used executable fixtures, not models.

## Can a coordinator use team memory to choose one reviewer?

**Separate registration, before the first delegation run.** The preceding packet has 3 sources but only 6 distinct sets of true route scores. A second report can add incorrect numbers. This study gives a live coordinator one reviewer request. Both conditions receive the same prior accuracy counts. Only one identifies which count belongs to which candidate.

- Use the same packet commitment. Reserve rounds 1 and 2 for history. Test rounds 3–6 of all 3 sources, each of 3 coordinator views, both A/B assignments, and 2 repetitions: 144 blocks. A prior numeric audit qualifies only if its report events, truth observations, and authenticated fetch all precede the current fetch in the same trial. Require 2 qualifying earlier rounds. Never read an earlier decision as feedback. In the third source, multiple fetches occurred before any decision.
- The coordinator sees its own 2-field report and the fields available from candidates `X` and `Y`. Each candidate represents one of the other 2 reports. A prior report counts as exact only if all 4 reported numbers match the independent previous observations. Show the 2 audit rows in ascending `(exact, checked)` order. Under `bound`, identify their respective candidates. Under `anonymous`, remove the candidate names from those rows. Do not show the current truth, report quality, source policy, round number, or either candidate's current numbers.
- Set X to the next report cyclically if `(policy + round + swap + rep)` is even; otherwise X is the previous report. Y is the other candidate. Run `bound` first when `(rotation + swap + rep)` is even. Run X first when both candidates are requested and `(policy + round + rotation + rep)` is even. These names are local to one block.
- Collect both real selections before contacting any reviewer. Call each distinct selected reviewer once. Its reply contains its vote and transcribed numbers. Give the coordinator a fresh final turn with its own report and the selected review, but no history, candidate name, or selection treatment. If both managers select the same reviewer, deliver the same actual final result to both policies. Never call an unselected reviewer. Never repair a malformed selection, review, or decision. Each complete block uses 4–6 first calls.
- Primary: correct final decisions under `bound` minus `anonymous`, matched within each block. Average within source, then weight the 3 sources equally. Report complete pairs and best/worst differences over all 144. Secondary: choice of the historically more accurate candidate when the counts differ; choice of a currently exact report; how often choices and final branches differ; and repeat disagreement. Report distinct paid turns and unknown charges separately. Do not count a shared final as 2 independent observations.
- Use the same direct Flash settings as above, with at most 864 calls under the existing ledger. This compares 2 real delegation policies with a limited review budget. It is not a test of closed-loop learning by the archived agents. The prior audits are new information that the study supplies.

Use `--workflow delegate` on the same runnable commands. The 2 trusted programs use the stdin/stdout contract above. The decision program handles selection (`{"ask":"X"}` or `{"ask":"Y"}`) as well as the final choice. The reviewer program receives no request if neither selector chose it. The commands below make no built-in provider calls:

```sh
python -m examples.seatlab.visibility.route_cli plan --workflow delegate
umask 077
private=$(python -c 'import tempfile; from pathlib import Path; print(Path(tempfile.mkdtemp()).resolve())')
python -m examples.seatlab.visibility.route_cli commands --workflow delegate \
  --config seats.json --study "$private" --start 0 --stop 1
python -m examples.seatlab.visibility.route_cli report --workflow delegate --study "$private"
```

Use `demo` instead of `commands` and omit `--config` for a no-model transport check. Use a new private directory for a new run. A registered block has one claim, even if a process fails. The report distinguishes unclaimed blocks, known unselected branches, missing replies, and identical selections that share one actual final response.

## Which record should a replacement agent read?

**Registered before the first handoff call.** A new agent inherits a short note from a departing agent. It can open only one current record before it must make its decisions. Does identifying the record whose revision changed improve its choices? This tests a context-handoff interface. It does not simulate an EVE mission or resume a model session.

- Use 12 new Dispatch Handoff seeds, `5801` through `5812`. From the first 8 native shipments, use IDs `1, 3, 5, 7`, named `P, Q, R, S`. Keep their actual old and current stock and utility ranks. Do not select by difficulty.
- Construct 5 handoffs per seed. In 4 of them, one different record changes at turnover. The writer sees its old stock. For the other 3 records, the writer sees current stock. In the fifth handoff, no record changes. The timing of these publications is an intervention, not an observed native event.
- Use 2 repetitions per case: 120 matched blocks, including 96 with a change. Each block starts with a new direct DeepSeek Flash writer. It sees only the old record contents and writes one note of at most 256 UTF-8 bytes. Both successor policies receive that same actual note. The writer sees neither utility ranks nor revision receipts.
- Both selectors see the record questions and the same number of changed and unchanged receipts. `bound` identifies the record for each receipt. `anonymous` gives only their sorted statuses. A selector chooses exactly one record to read. Alternate which policy goes first by case index and repetition. The final dispatcher receives the note, its selected current record, and the questions, but not the revision receipts.
- If both policies select the same record, deliver the same final prompt and use **one actual final response** for both. Otherwise request two independent final responses. Record each selected record and each returned choice. Never infer a missing answer or retry any stage. Each block uses at most 5 first calls, for at most 600 calls total.
- Primary: `bound − anonymous` in current optimal choices **for the changed record**. Match within case and repetition. Average within seed, then across all 12 seeds. Report complete coverage and sharp missing-data bounds over the fixed register. If a seed has no complete pair, omit the full-register point estimate. Secondary: selecting the changed record, all-record accuracy, unchanged controls, and cases where the old and current best choices differ.
- Use an independent copy of the unchanged stock scoring rule for observed final choices. These are host-side decisions on native worlds, not Harbor trial rewards. Share no private note or unrequested record in public evidence. Report direct usage separately from external harnesses.

### Run a handoff on your own records

Use Python 3.12 or later from the root of this fork. Start with the [two-desk rota](handoff/rota.json). Its revised opening hours change one answer. Neither Harbor, Docker, nor a model account is required for this first check.

```sh
module=examples.seatlab.handoff
packet=examples/seatlab/handoff/rota.json
digest=$(shasum -a 256 "$packet" | cut -d' ' -f1)
private=$(python -c 'import tempfile; from pathlib import Path; print(Path(tempfile.mkdtemp()).resolve())')
mkdir -m 700 "$private/study"
python -m "$module" plan --packet "$packet" --sha256 "$digest"
python -m "$module" demo --packet "$packet" --sha256 "$digest" --study "$private/study" --start 0 --stop 2
python -m "$module" report --packet "$packet" --sha256 "$digest" --study "$private/study" > "$private/report.json"
```

`demo` uses a fixed answer. It checks the interface, not model skill. To test two real harnesses, create a new study directory. Use `commands` instead of `demo`. Add `--config seats.json` using the [two-program protocol](#run-your-own-reviewer-and-decision-maker). `reviewer` writes the note. `decider` selects a record and answers the questions. Each invocation receives only its turn. Never pass the packet path, current rubric, or private journal to an agent. These programs are trusted local code, not a security sandbox.

To add a domain, copy the rota packet. Give each case 2 to 8 named records. Give each record an old body, a current body, revision IDs, one question, and its old and current correct choice. Revise at most one record per case. Group related cases under the same cluster. Pin the exact original SHA-256 before a study. Read reports with that same packet and digest.

Parallel workers can claim different indices in one private directory. `pending` lists untouched indices. Specify `--spend-dir` when continuing a direct study so an existing ledger entry also blocks a repeat. `report` never contacts a provider. Save reports **outside** the study directory.

To use Dispatch Handoff instead, omit `--packet` and `--sha256`. `packet` prints its exact input bytes. `stock "$private/report.json"` checks each observed Dispatch decision against the original native scorer. It cannot score a custom domain. `direct` uses only the registered Dispatch worlds and the existing DeepSeek spend ledger. An external agent program can have its own charges.

### Can a model pack an actual outgoing note better than a byte prefix?

**Separate experiment, registered before collecting any packing responses.** The original pilot showed that a valid JSON response can exceed a note budget. This study does not repair that trial or change its result. It asks whether a replacement can use a smaller transfer when the departing agent is no longer available.

- Enroll all 120 original writer slots above, in order. After the original run stops, seal the exact first response for each slot by hash. Never call the original writer again. Accept a nonempty, printable note from an exact `{"note":"..."}` response even if it exceeded the old limit. Keep missing and malformed sources on the register. The source note is the only input to either packer.
- The new limit is 96 UTF-8 bytes. `prefix` takes the longest complete prefix within the limit. `rewrite` makes one stateless model call on the original note, asking for a compact note that preserves identifiers and values. Apply the same byte limit to its answer. A malformed answer does not get a replacement. If the original already fits, both policies use it and neither starts a packing call.
- Give the successor the packed note, the questions and options, and the current body of the single revised record, if present. Give no body on a stable case. Do not reveal the strategy, budget, unrequested facts, or correct choices. If the two packed notes are identical, share one actual successor call. Otherwise alternate the first successor by case index plus repetition. Preserve first attempts and count missing transport separately.
- Primary: `rewrite − prefix` in correct choices on **unchanged** records. Compute the fraction within each block, then the mean within each native world, then the mean across 12 worlds. A correct answer to a disclosed revised record cannot improve this measure. Report complete pairs and missing-choice bounds. Do not present a 12-world estimate when any world lacks a complete pair. Report revised-record accuracy, shared answers, invalid sources, byte use, and actual call costs separately.
- Use the existing direct DeepSeek Flash settings and ledger. Each block can start 1 packing call and 2 successor calls at most: 360 total. The prefix policy has no packing-call charge. The experiment tests this cost tradeoff, not equal compute. Check observed default-domain decisions against the original native scorer. These are new decisions on fixed records, not new Harbor trials.

**Try it with your own completed handoff.** Keep the original private directory. Do not run its writer again. Use the same packet and exact digest as before. Omit `--packet` and `--sha256` only for the included Dispatch worlds.

```sh
packing=examples.seatlab.packing
original="$private/study"
python -m "$packing" seal --packet "$packet" --sha256 "$digest" --original "$original" > "$private/seal.json"
seal=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["source_sha256"])' "$private/seal.json")
mkdir -m 700 "$private/packing"
python -m "$packing" commands --packet "$packet" --sha256 "$digest" --source-sha256 "$seal" \
  --original "$original" --study "$private/packing" --config packing-seats.json --start 0 --stop 2
python -m "$packing" report --packet "$packet" --sha256 "$digest" --source-sha256 "$seal" \
  --original "$original" --study "$private/packing" > "$private/packing-report.json"
```

Use the same two-program configuration format as `handoff commands`. The `reviewer` now rewrites notes. It sees no questions, records, or answer key. The `decider` receives one packed note per decision. You may replace either executable. Give parallel workers disjoint indices. A changed source digest stops the run. The public report contains response hashes and choices, not raw notes.

### Repeat the handoff comparison on saved native Harbor trials

**Recorded-source replication, registered before new model responses.** For each of the 4 registered mixed-roster seeds, select the first scored `reordered` trial in the published register. This selects seeds `4001`, `4003`, and `4007`. Seed `4013` has no eligible trial. Use every shipment ID, `0` through `5`. Do not select on score, disagreement, or whether a shipment changed.

The writer receives the **accepted native inventory reports**, not reports regenerated from world truth. One case per shipment exposes its revision 1 report at turnover. All other reports come from revision 2. Include a stable case and 2 repetitions. The 3 original trials yield 42 new handoffs, including 36 with one revised record. Their timing is a replay intervention. Original dispatch choices are not inputs. Retain incorrect sender reports if present. Score against true current stock, not what the sender said.

Repeat the same `bound` versus `anonymous` comparison and primary measure above. Use new DeepSeek writers and successors, the same note limit, and the same sharing rule for identical final requests. Report exact support, cost, and missing bounds across the 3 observed worlds. Do not extrapolate to the missing fourth world. Do not combine this result with the 12 newly generated worlds. No original model, native trial, or Docker container needs to restart.

**Use a completed Harbor trial.** `trial` is a Dispatch Handoff trial directory with `result.json` and its world artifact. Repeat `--trial` for more sources. Keep their order fixed. `--ids` gives the 2 to 8 shipments you want to replay. A pooled trial has no sender messages and cannot be a source.

```sh
native=examples.seatlab.handoff.native
module=examples.seatlab.handoff
python -m "$native" packet --trial "$trial" --ids 0 1 > "$private/recorded.json"
python -m "$native" receipt --trial "$trial" --ids 0 1 > "$private/source.json"
digest=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["packet_sha256"])' "$private/source.json")
mkdir -m 700 "$private/recorded-study"
python -m "$module" commands --packet "$private/recorded.json" --sha256 "$digest" \
  --study "$private/recorded-study" --config seats.json --start 0 --stop 2
python -m "$module" report --packet "$private/recorded.json" --sha256 "$digest" \
  --study "$private/recorded-study" > "$private/recorded-report.json"
python -m "$native" stock --trial "$trial" --ids 0 1 --sha256 "$digest" --report "$private/recorded-report.json"
```

The [published 3-trial packet](results/handoff-recorded-packet.json) also works with `plan`, `demo`, `commands`, and `report` without the originals. Use the digest from its [source receipt](results/handoff-recorded-sources.json). Do not give the packet to a model: it contains the scoring answers. Give the agent only the role prompt. Local stock verification requires the original trial directories. A reconstructed input is not proof that the original agent acknowledged it. Paid direct mode requires both original acknowledgments.

### Does assigning answer ownership prevent incomplete handoffs?

**Registered before new calls.** All 48 invalid final replies in the 12-world handoff run answered only for the record the agent opened. They contained a valid option for that record. This experiment tests a change to the work assignment, not a new parser for old answers.

- Enroll the original 120 slots. Freeze the exact final request made after the named-receipt policy chose its record. Include shared finals once. There are 99 such requests and 21 slots without one. Do not select on what the original final answer contained. Never restart a writer, selector, or original decision.
- Give each policy the same actual note, questions, options, and opened record. `batch` repeats the original user prompt verbatim. `checklist` also names all required answer IDs and says that opening one record does not remove the others. `owners` assigns one independent worker to each ID. Each owner sees the same facts and all questions, but must submit only its assigned answer. Combine only valid submissions. No worker sees another worker's answer.
- Rotate the order of the 3 policies by case index and repetition. Rotate the owner order by the same index. Each eligible request starts 1 batch call, 1 checklist call, and 4 owner calls. This permits 594 direct calls. There are no retries. Keep transport failures and missing responses distinct from received invalid answers. Owners use more compute. The first comparison does not.
- Primary: difference in complete, contract-valid team delivery, `checklist − batch`. Secondary: `owners − checklist`. A malformed received submission is a known delivery failure. An unobserved submission is unknown unless another component already makes completion impossible. Compare the policies within each eligible request. Weight each of the 12 source worlds equally. Report eligible counts, paired coverage, and sharp bounds. These are not confidence intervals.
- Also report how many records each valid submission answered, native stock accuracy on completed deliveries, and correct delivered choices per eligible record. Count no choice from a rejected whole-map answer. Count valid owner submissions separately even if another owner fails. The stock check must flag any disagreement on an observed choice. Report actual distinct model calls and costs per strategy. No team receives a truth value, an original final answer, or other unopened stock.
- A work index identifies one physical call. Different local workers can claim disjoint indices. The immutable source digest and the original spend ledger block repeat charges after an interruption. Reports never publish role prompts or private notes.

To compare output strategies on your own handoff, use the same packet, digest, and original private study. The following commands use no built-in provider:

```sh
coverage=examples.seatlab.coverage
original="$private/study"
source_args=(--packet "$packet" --sha256 "$digest" --original "$original")
python -m "$coverage" seal "${source_args[@]}" > "$private/coverage-source.json"
seal=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["source_sha256"])' "$private/coverage-source.json")
source_args+=(--source-sha256 "$seal")
python -m "$coverage" plan "${source_args[@]}"
mkdir -m 700 "$private/coverage"
study_args=(--study "$private/coverage")
python -m "$coverage" commands "${source_args[@]}" "${study_args[@]}" --config seats.json --start 0 --stop 2
python -m "$coverage" pending "${source_args[@]}" "${study_args[@]}"
python -m "$coverage" report "${source_args[@]}" "${study_args[@]}" > "$private/coverage-report.json"
```

Use Bash for this example. Set `decider` to your batch and checklist program. Set `reviewer` to the program that answers for one record. Each `plan` index represents one real process call, not one whole team. Start a second worker on a disjoint range. A claim remains occupied after an interrupted call. For paid direct runs, always give `pending` and `report` the original `--spend-dir`. `stock` checks the included Dispatch domain, not custom packets. The [source seal](results/coverage-source.json) lists the 99 observed requests and the 21 slots without one; it contains no raw notes or answers.

### Can a different subscription harness use the same recorded trial packet?

**Registered before collecting subscription teams.** Use the [recorded packet](results/handoff-recorded-packet.json) without modifying its rubric or its role prompts. In each of its 3 worlds, test the first revised case and the stable control, with both registered repetitions. This gives 12 handoffs and at most 60 new first calls. Do not select cases by the previous teams' answers.

Use the same explicitly named subscription model for both new roles through a trusted external command. Give it no tool, earlier conversation, API key, packet, or private journal. Reject a reply when the served model differs. Retain a missing call; do not retry or switch providers within the cohort. Record prompt and response commitments and service-reported tokens separately from the original Harbor trial. Primary: how many of 12 writer turns yield a valid note, then how many named and anonymous successors deliver a complete decision. Check accepted choices against the stock scorer. This small replication tests portability and contract completion, not relative model quality.

The optional `visibility/omp_seat.py` command uses the signed-in OMP subscription gateway. Each process sends one new text request. It rejects tools, truncated replies, and a different served model. It needs no API key. The executable is trusted local code, not a sandbox. Set `model` to a concrete `openai-codex/...` model included in your account. Do not enable a paid fallback.

```sh
umask 077
mkdir -m 700 "$private/writer" "$private/successor" "$private/subscription-study"
adapter="$PWD/examples/seatlab/visibility/omp_seat.py"
jq -n --arg python "$(command -v python)" --arg adapter "$adapter" \
  --arg omp "$(command -v omp)" --arg home "$HOME" --arg model "$model" --arg dir "$private" \
  'def seat($r): {argv:[$python,"-I",$adapter,"--omp",$omp,"--home",$home,
    "--model",$model,"--receipts",($dir+"/"+$r)],env:[]};
    {reviewer:seat("writer"),decider:seat("successor")}' > "$private/seats.json"
python -m examples.seatlab.handoff commands --packet "$packet" --sha256 "$digest" \
  --study "$private/subscription-study" --config "$private/seats.json" --start 0 --stop 1
```

Use absolute physical paths. Each receipt contains the request and reply hashes, served model, and reported token counts. It does not contain the text or credentials. The study journal is private and **does** contain the actual replies. Do not publish the journal. The run shown above starts one real team, not the full registered cohort. It does not report a cash price for a subscription.

After `handoff report`, verify calls without reading any reply text:

```sh
python -m examples.seatlab.handoff.external_audit "$private/report.json" \
  --writer-receipts "$private/writer" --successor-receipts "$private/successor" \
  --model "$model" --indices 0
```

Give the exact block indices for your run. The auditor rejects missing or surplus success receipts, a different served model, and attempted blocks outside that selection. It counts repeated identical requests and replies as separate calls only when there are separate receipts.

#### Does the reader use named receipts when the first record is not revised?

**Registered after the 12-block portability test, before collecting this extension.** Both policies opened the first record in every portability block. Its 6 revisions all left the correct bay unchanged. Enroll all 30 remaining blocks in the same public packet, not just blocks with favorable earlier outcomes. Every revision in this set affects a record outside the first position. Keep the 12 previous blocks out of the new cohort. Use a fresh private journal, the same explicitly named subscription model, and the same role prompts. Do not retry. Stop if the subscription cannot serve that model.

Primary: difference between the 2 policies in how often they open the revised record, paired within a valid writer note. Give each of the 3 original worlds equal weight. Report coverage and bounds for missing selections. Secondary: how often the resulting decision gets that record right when the revision changes the best bay. Identify these cases from the frozen scoring packet, not from either agent's choice. Report each policy's first-record selections and the number of shared final draws. The anonymous reader does not have the record identity. A first-record choice alone is not proof that it ignored available evidence.

## How quickly does a team stop trusting a stale source?

**Registered before the first model call.** The preceding Crucible experiment supplied prior accuracy. It did not let the team learn from its own choices. Here, two independently callable agent roles make decisions on new Dispatch Handoff worlds. Each choice affects the information the next selector receives. The world generator and stock scoring rule are unmodified. This is a host-side experiment, not a native Harbor trial.

- Use all 12 seeds `5201` through `5212`, both source regimes, and 2 repetitions. This gives 48 paired blocks. Use all 10 odd shipment IDs among the first 20 shipments. These are exactly the shipments on which the native stock changes. Do not filter by payoff or by model output.
- Name the two inventory desks X and Y. One desk sees current stock. The other sees the previous stock for the same shipment. X starts with current stock if `seed + repetition` is even. Otherwise Y starts with it. In `stable`, this assignment never changes. In `switch`, it reverses on step 6, counting from 0. Do not announce the reversal.
- Calibrate at steps 0 and 1. Request each desk once. X goes first when `floor((seed − 5201) / 2) + repetition` is even. Both policies receive the same two actual audits. The reviewer transmits its observed pair of available bays. A separate dispatch call chooses a bay using the reported pair and the native utility ranks.
- From steps 2 through 9, a selector chooses one desk. Both policies receive the same form of named audit counts. `lifetime` counts all previous completed decisions in that policy. `recent` counts only its last 2 completed decisions, across both desks. An audit is exact when the actual reported pair matches current stock. Record it only after the decision. Do not show current stock, the other desk's report, or future feedback. A desk with no observations gets a denominator of 0.
- Contact only the selected desk. Give the final decision maker the actual returned pair and the utility ranks, without any audit counts. Accept a valid bay even when the desk did not report it. Each policy gets independent generations after calibration. Start `lifetime` first when `seed + repetition + step` is even. An invalid stage ends that policy's remaining decisions. A shared calibration failure ends both. Keep incomplete blocks; never retry a requested turn.
- Primary: percentage-point difference in optimal choices, `recent − lifetime`, over steps 6–9 in the `switch` regime. Match within seed, repetition, and step. Average complete differences per seed and weight all 12 seeds equally. Report no full-register point estimate if any seed has no complete pairs. Also report sharp missing-decision bounds over the fixed register. These are not confidence intervals.
- Secondary: the same contrast in `stable`; selection of the now-current desk at each post-switch step; accuracy where the changed stock also changes the best bay; pre-switch differences; and report copying errors. Each regime has at most 96 final choices per policy after the cutoff. These are 12 native seed worlds, not 96 independent tasks.
- Use direct DeepSeek Flash with the existing spend directory, at most 52 first calls per block and 2,496 in total. The private runner commits to the exact prompts and first replies. An independent stock check scores only observed model choices. Its fixed-bay reference probes are not agent actions or native trial rewards.

### Run different agent programs on the same stock changes

This command uses the checked-in native world. It needs Python 3.12 or later. It does not need Docker, the private archive, or an API key. Use the same absolute `reviewer` and `decider` programs as in [the two-program example](#run-your-own-reviewer-and-decision-maker).

```sh
module=examples.seatlab.visibility.feedback_cli
private=$(python -c 'import tempfile; from pathlib import Path; print(Path(tempfile.mkdtemp()).resolve())')
mkdir -m 700 "$private/study"
python -m "$module" plan --seed 7001
python -m "$module" commands --seed 7001 --study "$private/study" --config seats.json --start 0 --stop 1
python -m "$module" report --seed 7001 --study "$private/study" > "$private/report.json"
python -m examples.seatlab.visibility.feedback_stock "$private/report.json"
```

Your executables can incur their own provider costs. For a free transport check, use `demo` instead of `commands` and omit `--config`. For the registered study, omit `--seed`. Repeat `--seed` for a custom set of worlds. Use the same seeds and order when reading the study.

Separate workers can use disjoint block ranges in one private directory. Keep an interrupted block closed. Use `report` to find untouched blocks. The built-in `direct` mode uses only the registered seed set and requires the original spend directory.

For a direct run, add `--spend-dir "$SEATLAB_SPEND_DIR"` to `report`. This compares the role, model, and token counts with the original host ledger. Do not use that ledger to price an external executable or a subscription. Save report files outside the private study directory.

## Receiver visibility: stored teams and fresh model decisions

These are 3 different measurements. Do not pool their denominators.

1. **Stored evidence.** Replay the named Flotilla decomposition panel (276 trials), the muster E1/E2 panel (210), and the completed Crucible panel (130). The counts include solo and no-channel controls. For each receiver, remove only messages from other roster seats. Keep self and world messages, sender actions, other receivers, and the original action order. Count availability, successful fetch, and proven model input separately. A missing trace is not an empty inbox. Replaying a recorded action does not measure the reward under a different message policy.
2. **Fresh Harbor teams.** Use Dispatch Handoff seeds `3101,3103,3109,3119`. Run 3 repetitions per seed, with 4 shipments per trial. Each pair has one `fresh` and one `silent` world. Use fresh first when the zero-based seed index plus repetition is even. Both seats make a new model call in both arms. Dispatch waits for the inventory submission in both arms. Omit the condition name from both model prompts. Only the visible inventory messages differ by design. The registered primary measure is the paired difference in the native score, averaged within each of the 4 seed blocks before taking the mean. Report the 4 block differences, the number of complete pairs, and repeat-to-repeat variation in submitted choices within each arm. Retain failed first attempts as unresolved. The 96 scheduled shipment decisions are not independent worlds.
3. **Blinded listeners on stored evidence.** Use the 3 Crucible scout policies `p00,p50,p100`, in that order. Within each, sort distinct native trial UUIDs by `sha256("seatlab-listener-v1:" + uuid)` using the lowercase, hyphenated UUID. Select the first 2. Use round 1 for the first and round 2 for the second. Require a decision after a receiver fetch containing all 3 scouts' reports. Use the last such round fetch before the first decision; require that this last fetch is complete. Require recoverable numeric claims and independent, consistent private observations for the answer key. Do not replace an ineligible selection. Send only allowlisted numeric reports, never the archived free text, answers, identifiers, or answer key. Swap the 2 route labels when the zero-based policy index plus selection index is odd. Use the same labels in both arms. Compare all fetched reports with no reports. Ask a new listener 3 times per case and arm. Interleave arm order across repetitions. Score only an exact `A` or `B` decision after whitespace removal at the ends. Report unresolved responses, accuracy per case and arm, and disagreement between repeats of the same prompt. These are new single decisions, not new native team trials.

Both model experiments use the first-party ChatGPT subscription route for `gpt-6.2-sol`. They do not use an API key, credits, or a paid fallback. Use a fresh session without tools per call. If the route cannot attest the model or remaining included use, stop that cohort. Never retry failed cells. Record any later model or protocol change as a separate cohort before collection.

### Direct DeepSeek cohort amendment, before its first call

The subscription cohort made 0 model calls. Its admission check did not establish that a call could use only included credit. The following **separate cohort** keeps the registered cases, contrast, first-attempt rule, and order. It changes the model and transport. Do not combine its observations with the subscription cohort.

Use the direct `api.deepseek.com` endpoint and the `deepseek-flash` model for both roles. The provider lists `DeepSeek-V4.1-Flash-1017` as the served revision. Record the model string returned with each generation, separately from that published mapping. Use temperature `0.7`, thinking disabled, JSON output, no tools, no session history, and at most 512 output tokens. The native speaker returns revision reports; the native listener returns dispatch choices. The conditional listener returns exactly `{"choice":"A"}` or `{"choice":"B"}`. The same rule applies to both visibility arms.

The budget allows at most 48 calls for 24 fresh native trials and 36 calls for 6 historical cases with new listeners. Check the direct account before every generation. Reserve $0.01 per call under a $1 local cap. Stop before crossing the shared $10 cap or the $1.50 balance floor. Keep the full reservation for an ambiguous call. Never send credentials to Docker or store them in trial artifacts. Record each run's token-based cost separately from the account balance. Apply the [published Flash prices](https://api-docs.deepseek.com/quick_start/pricing) for the request interval. The account balance alone does not identify one run's cost.

### Can a change notice help a live team spend two reads?

**Registered before the first native trial.** The handoff replay names one changed record. A native team can have several changed reports but only enough time to open 2. Test whether the dispatcher uses names to select useful updates and earn a better native reward. Do not replace the Harbor world, verifier, or either agent with a reference policy.

- Use the `reordered` Dispatch Handoff task with 6 shipments, seeds `6401` through `6406`, and 2 repetitions. Start a new 2-seat Harbor team in both arms of every pair. Run `named` first when the zero-based seed index plus repetition is even. There are 24 native trials and at most 72 direct DeepSeek calls.
- The inventory model submits both reports for each shipment through its real seat. Compute a change notice only when the **sender's submitted sets of bays** differ across its 2 revisions. Do not use private stock or the best dispatch choice to issue a notice.
- The dispatch model sees the actual revision 1 reports, utility ranks, and the number of reports with changed content. In `named` it also sees which shipments changed. Map native shipment IDs to `Q1` through `Q6` in increasing SHA-256 order of `seatlab-attn-v1:{seed}:{repetition}:{id}`. Use the same mapping in both arms. Never show the seed, native ID, or second payload before selection.
- Ask the model to open exactly 2 distinct shipments. In a fresh generation, give it both selected revision 2 payloads, the original reports for all 6 shipments, and the utility ranks. Do not repeat the notices, the arm name, or the selector's explanation. Submit its 6 actual bay choices to Harbor, even when a choice is suboptimal. Use the same direct model and budget in both arms. Each valid team makes 1 inventory call and 2 dispatch calls. Keep each first attempt; never backfill a missing result.
- Primary: the paired difference in **native Harbor reward**, `named − count`. Average over complete repetitions within each world, then give all 6 worlds equal weight. Omit the full-register point estimate if any world has no complete pair. Compute finite bounds over all 12 pairs, using each observed reward and allowing an unknown reward in `[0,1]`. These are not confidence intervals.
- Secondary: which of the 2 reads covered a changed **submitted** report, which opened shipments had a different true optimum, the number of optimal and feasible decisions, and actual sender transcription errors. Check final actions against native stock. Independent senders can disagree within a pair: this estimates a team policy, not a contrast on one frozen note.

**Separate external replication.** After one direct native team, register the same complete grid for two keyless subscription seats. Use `openai-codex/gpt-6.2-sol` for each role. Start each turn with an empty conversation, no tools, and no paid fallback. Use separate receipt directories for inventory and dispatch. Do not pool the 2 transports. Report the within-transport native reward difference, first-attempt coverage, service-reported model and usage, and stock agreement. Do not give a subscription a cash price. Repeating worlds does not create new independent worlds.

#### Run two real seat programs

Use Python 3.12 or later in the fork environment. Use an executable from this fork with native `participants` support. The stock task, not either model, decides the reward. Each external seat accepts the [one-line JSON program interface](#run-your-own-reviewer-and-decision-maker). Set `reviewer` to the inventory program and `decider` to the dispatch program. Configure both programs yourself; they can incur provider costs. For included subscription use, follow the [keyless command example](#can-a-different-subscription-harness-use-the-same-recorded-trial-packet).

```sh
export PYTHONPATH="$PWD:$PWD/src:$PWD/examples:$PWD/examples/seatlab"
module=examples.seatlab.attention
private=$(python -c 'import tempfile; from pathlib import Path; print(Path(tempfile.mkdtemp()).resolve())')
python -m "$module" plan
python -m "$module" seal --study "$private/team" --transport commands --config "$config"
python -m "$module" pending --study "$private/team"
python -m "$module" run --study "$private/team" --harbor "$harbor" --cell attn-s6401-r0-named
python -m "$module" collect --study "$private/team" --cell attn-s6401-r0-named
python -m "$module" report --study "$private/team" > "$private/report.json"
python -m "$module" verify "$private/report.json"
```

Set `$harbor` and `$config` to physical absolute paths. `seal` creates the study directory. One `run` starts one real Docker trial. Assign both arms of a pair to one worker to preserve their registered order. Give separate pairs to different workers. Each worker claims its trial before launch. After interruption, `pending` excludes occupied trials. `report` checks native evidence again and leaves unknown rewards out of the point estimate. Never publish the private native directories: they contain the model replies. The public report contains shipment decisions and text hashes, not the text or credentials.

When reading an older study, pass its original full commit SHA to `collect` or `report` with `--source-revision`. The reader checks the registered source bytes against local Git objects. It does not check out or execute old code. It still recomputes the current prompt hashes and native scores. If the task or prompt contract changed, use the original checkout. The option cannot start a new trial.

For the two published attention cohorts, the original seat commit is `90fd98b787f1be6cc919c38daedcbf2d49a0eecf`. To compare an exported report with original native folders, run `python -m examples.seatlab.native_stock REPORT --study "$private/team"` from a Python environment with stock Eval Lab installed. The reader uses `evallab.results.load_trial` without a database or model call.

### Can a six-letter brief help another agent prioritize revisions?

**Registered before the first briefing trial.** A named change notice helped DeepSeek select reports. That study let the *dispatcher* choose its own updates. This experiment moves that decision to the *inventory agent*. The inventory agent knows which bays changed but not which bay the dispatcher wants. Test whether a small return channel helps.

- Start 24 new native Dispatch Handoff teams on unseen seeds `6501` through `6506`. Use 6 shipments and 2 repetitions per world. Run both `brief` and `masked` with independent live agents. Put `brief` first when zero-based world index plus repetition is even. The stock world and its verifier do not change.
- Inventory submits its model's actual reports to the world first. Dispatch uses only its own utility ranks to write one preferred bay letter per shipment. Hide the native shipment IDs behind the same `Q1` through `Q6` mapping in each pair. Both arms pay for this generation. In `brief`, release exactly those 6 letters. In `masked`, release no letters.
- Inventory makes a second decision from its own submitted reports and the allowed brief. It chooses exactly 2 current reports to send. A fresh dispatch call gets the 2 selected payloads, the first reports, and the utility ranks. It does not see the brief or arm. Record its 6 choices in the real world. Do not forward any explanation, score, unopened payload, or credentials.
- Both seats opt into one trial-specific, host-side channel in their participant configuration. It carries only the brief and the return selection. This is an **experimental team channel**, not a message recorded by the stock world. Keep role-specific prompt and response hashes, native view acknowledgments, decision hashes, and matching records at both ends. Never pass the host channel or private directories to an external model.
- Primary: `brief − masked` in actual stock-verified Harbor reward. Average matched repetitions within each world, then all 6 worlds equally. Report all 12 pair bounds with unknown rewards in `[0,1]`. Secondary: valid brief letters matching the recipient's actual highest utility, changed-optimum reports sent, sender transcription errors, feasible and optimal choices. Count missing and malformed first responses without retries. At most 96 first-party DeepSeek calls. Treat a comparison with another protocol or model as descriptive, not randomized.

**Separate command-seat replication, registered before either transport runs.** Run the same 24 briefing teams again with 2 keyless `openai-codex/gpt-6.2-sol` programs. Start each turn in a new session without tools. Keep inventory and dispatch service receipts separate. The 6 worlds remain the same. Compare `brief − masked` within each transport; never pool transports or count them as 12 worlds. The subscription cohort makes no paid API calls. Keep failed or missing first attempts.

Use `module=examples.seatlab.briefing` in the two-program commands above. The first registered cell is `brf-s6501-r0-brief`. Direct enrollment requires `--transport direct --spend-dir "$original_private_ledger"`. A direct launch requires an authorized first-party DeepSeek key. Use a separate private directory and no paid key for command seats. The relay is process-local to an explicitly declared pair. It is not a production network transport or general multi-agent messaging service.

### Replay the original server evidence

Run these commands from the fork root with Python 3.12 or later. No model, SQL service, Docker, or Harbor installation is needed for the stored projections.

```bash
python -m examples.seatlab.visibility flotilla "$FLOTILLA_JOBS"
python -m examples.seatlab.visibility muster "$MUSTER_E1RUN"
python -m examples.seatlab.visibility crucible "$CRUCIBLE_ROOT"
python -m examples.seatlab.visibility crucible-timeouts "$CRUCIBLE_ROOT"
```

- **Flotilla:** Point to the original `.flotilla-local/jobs` directory. The adapter selects the `c1`, `w1`, and `w2` jobs in the published decomposition panel. Later waves and duplicate exports are not substitutes.
- **Muster:** Point to the `e1run` directory containing the 7 registered job directories. Each job has 10 task instances and 3 attempts per instance. The server's final verifier copy takes priority over an earlier artifact copy.
- **Crucible:** Point to the tree containing `experiments/report_final5/report.json` and its retained trial directories. The adapter validates the 138 source records against their manifests. It keeps the 130 non-timeout trials separate from the 8 timeout trials.

Each command prints aggregate JSON. It verifies all declared input files with SHA-256 before and after replay. A wrong panel, missing required identity, duplicate trial, or changed source stops publication. Incomplete event coverage remains explicit. A receipt count of 0 is not proof that an uninstrumented event never happened.

**For a different Harbor task:** Adapt its first-party trial manifest and ordered server log to [`Trial` and `Event`](visibility/core.py). Use `available` for mailbox eligibility, `fetch` for peers returned to an authenticated receiver, and `bound` only for messages present in the captured model request. Use private message IDs, not message text. Maintain action order within each seat. Mark each seat's coverage `complete`, `partial`, or `unavailable`. Call `project(trial, muted_seat=receiver)` to make one change, or `summarize(trials)` to count the recorded changes by arm. This works without a change to Harbor's runtime.

For the 2 source-specific outcome comparisons, run:

```bash
python -m examples.seatlab.visibility.boxes_behavior "$MUSTER_E1RUN"
python -m examples.seatlab.visibility.stored_listener_variation "$CRUCIBLE_ROOT"
```

### Run a new blinded listener experiment

`fingerprint`, `seal`, and `report` are offline. `run-cell` makes a **paid** direct request. Use a private study directory and a separate private spend directory. Record the verified opening balance and your own budget before the first call. Do not delete or reset the spend directory to bypass a budget or retry a failed call.

```bash
python -m examples.seatlab.visibility.listener fingerprint "$CRUCIBLE_ROOT"
python -m examples.seatlab.visibility.listener seal "$CRUCIBLE_ROOT" "$STUDY"
mkdir -m 700 "$SEATLAB_SPEND_DIR"
export SEATLAB_SPEND_DIR
python -m examples.seatlab.visibility.spend init "$SEATLAB_SPEND_DIR" \
  --opening-balance-usd "$OPENING_BALANCE_USD" --lane-cap-usd "$CAP_USD"
keys run -- python -m examples.seatlab.visibility.listener run-cell "$CRUCIBLE_ROOT" "$STUDY" 1
python -m examples.seatlab.visibility.listener report "$CRUCIBLE_ROOT" "$STUDY"
python -m examples.seatlab.visibility.spend report "$SEATLAB_SPEND_DIR"
```

`keys run --` supplies `DEEPSEEK_API_KEY` to the host process. Each numbered cell accepts one first attempt. The report separates an unrun cell, a reserved cell without a result, and a completed choice. The public report does not contain the answer key, prompt text, account balance, or private native trial identifiers.

### Run 2 new model seats in Harbor

Use the fork in a project-local virtual environment, with Docker running. Do not replace a global Harbor installation. Set `HARBOR` to the absolute path of the fork's `harbor` executable. Use the Python from the same environment. From the fork root:

```bash
export PYTHONPATH="$PWD/src:$PWD/examples/seatlab:$PWD/examples:$PWD"
python -m examples.seatlab.visibility.native_pairs plan
python -m examples.seatlab.visibility.native_pairs preflight --native-only --harbor "$HARBOR"
```

Neither command makes a model request. **The next command can charge for 2 direct DeepSeek calls.** Use a private output directory outside the checkout. Initialize the spend directory as above, once for the whole study. Start Docker trials one at a time:

```bash
keys run -- python -m examples.seatlab.visibility.native_pairs run \
  --out "$NATIVE_STUDY" --harbor "$HARBOR" --cell s3101-r0-fresh
```

Use the order from `plan` for the remaining cells. Keep failed first attempts. The following command reports what ran, even for an incomplete study. It makes no calls:

```bash
python -m examples.seatlab.visibility.pair_report "$NATIVE_STUDY"
```

To compare native token fields with the direct receipts, run this from the fork root with **Eval Lab's Python**:

```bash
PYTHONDONTWRITEBYTECODE=1 "$EVAL_LAB/.venv/bin/python" \
  -m examples.seatlab.visibility.native_usage --study "$NATIVE_STUDY" \
  --spend-dir "$SEATLAB_SPEND_DIR" --evallab-root "$EVAL_LAB"
```

This calls the stock raw trial reader, not a paid Eval Lab command. It does not change a trial or write to Eval Lab. Provide the same spend directory that the study used. A provider receipt names the cell and role but does not include a native trial UUID.

### Label-crossover follow-up, before its first call

The first listeners chose A on all 18 no-report requests. Accuracy alone could not separate a preference for that label from evidence use. This follow-up asks whether a listener chooses the **same physical route** when its label changes. It uses new generations. Do not combine them with the first 36.

Use all 6 cases in the original sealed schedule `b95aa3434271a357cd297d8a56b1715ff8edae1fd4a8566441b4a0995c1dedda`. Show each case under the original mapping and its exact A/B complement. Keep the same scores, display order, instructions, direct model, and settings. The no-report prompt remains identical in both mappings. Switching the mapping also switches the private answer key. Never send that key to the model.

Run 3 repetitions for each case, map, and arm: 72 new first attempts. Visit cases in the original order, then each repetition. Use the original map first if the zero-based case and repetition indices sum to an even number. Within a map, use the visible arm first if those indices plus the map index sum to an even number. Map index 0 means original; 1 means complementary. Never retry an invalid or missing call. Keep the same cumulative $1 local cap and $1.50 account floor; do not start a new budget.

For each case, repetition, and visibility condition, pair the choices under opposite mappings. Changing the letter preserves the physical route. Keeping the letter changes it. Report both counts over complete pairs, separately with and without evidence. Report A choices, accuracy, and missing cells in each arm and mapping. These are 6 source cases and dependent comparisons, not 72 independent tasks.

To reproduce, use `examples.seatlab.visibility.label_crossover` instead of `examples.seatlab.visibility.listener` in the commands above. Give it a new private study directory but the same spend directory. Its sealed schedule SHA-256 is `ed95b623c379a753fef313e39e02e612df1d69ffc986845d8484fcd47bc85e1f`. Run cells 1 through 72 in order. To read only this cohort's costs, use `python -m examples.seatlab.visibility.spend report "$SEATLAB_SPEND_DIR" --run-prefix mirror-`. Filtering the report does not reset the budget.

### Give a receiver from another harness the same input

The four files under `results/dispatch-s*-r1.json` are portable synthetic worlds. Each comes from repetition 1, the first repetition complete for all 4 seeds. The exporter checks both original native trials, the actual sender submissions, the reconstructed receiver text, the receiver decisions, and the stock scores. No private trial directory or Harbor install is needed to use a published packet.

**Do not give a model the packet.** It contains the synthetic world, original decisions, and scores. Give the model only the output of `prompt`. Use the packet's `.system` as the separate system instruction. The native commitment covers the exact **user text**, not the system instruction or the HTTP body.

```bash
module=examples.seatlab.visibility.dispatch_packet
packet=examples/seatlab/results/dispatch-s3101-r1.json
digest=1e776f57ea691d88bdf81baed6a65cd5a4d23e66dcaae3fcae90044425dc770b
python -m "$module" verify "$packet" --expect-sha256 "$digest"
python -m "$module" prompt "$packet" fresh --expect-sha256 "$digest" > user.txt
# Send user.txt to your receiver. Save its JSON response in choices.json.
python -m "$module" score "$packet" fresh "$(cat choices.json)" --expect-sha256 "$digest"
```

Run `export "$NATIVE_STUDY" --pair 3101 1` with the original native interpreter to produce those exact packet bytes. `verify` checks consistency. An external SHA-256 binds the bytes to a published version. Neither check independently authenticates a provider. Scoring a new receiver does not turn its choices into original Harbor actions.

### Can a receiver choose which missing update to request?

**Registered before the first new request.** A complete message channel tests delivery. A bounded message channel also tests whether the receiver can decide what to ask. Keep the real sender's submissions fixed. Compare model-selected delivery with a simple rule that serves an unread update. The comparison uses the same number of generations and deliveries. Like [Project EVE's historical market replay](https://www.anthropic.com/research/project-eve-claude-plays-mmorpgs), this cannot measure how the original sender would respond to the new receiver.

- Use repetition 1 for all 4 seeds. Use the posted inventory reports, not ground truth. The two input revisions were both **submitted**; the original native `fresh` dispatcher received revision 2 only. This follow-up constructs a new message schedule. It does not claim the original dispatcher received the new input.
- Hold back revision 2 on exactly 2 of 4 shipments. Use all 6 unordered pairs of held-back updates once per world, arm, and service window. Initially deliver all 4 revision 1 reports and the other 2 revision 2 reports. Each receiver sees the same instructions, utilities, and case identifiers. It does not see the hidden stock, seed, mask, arm, or original choices.
- Both arms first ask the same model to select 1 of the 4 cases to refresh. In `active`, deliver the selected case's submitted revision 2 report. In `queue`, deliver one of the unread reports. Choose the member at position `(seed_index + mask_index + window_index) mod 2` in the sorted pair. The model can spend its opportunity on an update already visible. Deliver exactly 1 report, including a repeat if selected. Tell the receiver which report arrived. Do not tell it the strategy or show its first-turn explanation.
- Ask for final dispatch decisions under the same instruction in both arms. Call the same direct model twice per complete session, with no memory other than the explicit role view. Keep the receiver's actual decisions, including feasible but suboptimal choices. Never retry a model call or substitute a missing result.
- Open the first service window at `2026-11-20T21:10:00Z`. Open the next two 180 and 330 minutes later. Each window lasts 30 minutes. Do not start an out-of-window turn or backfill a missed window. Alternate the first arm by the parity of `window_index + seed_index + mask_index`. There are 144 scheduled receiver sessions, 72 matched contrasts, and at most 288 direct requests. Use the **existing** spend directory and cumulative $1 local cap.
- Primary: fraction of optimal final choices, `active − queue`, paired within world, mask, and window. Average complete contrasts within each world; weight the 4 worlds equally. With no complete contrast in a world, do not present a 4-world estimate. Report every available denominator and window separately. Secondary: feasible choices, requests for updates not yet delivered, and cases where the two delivery rules select the same update. The 4 worlds—not the 576 shipment decisions—bound generalization. The shared provider and adjacent times can also correlate replies.

The [sealed plan](results/clarification-plan.json) has SHA-256 `aff5184e812e567db5939a058b43ca9097bb45adb30471394a6d3412dcc31171`. Supply a **physical absolute path**, without any symlink component, to the parent of your private output. On macOS, `/tmp` is a symlink. Use `realpath` to resolve it first. For a new replication, choose your own future start time. You can still read the published packets and results after a service window closes.

```bash
packets=$(realpath examples/seatlab/results)
study="$(realpath "$PRIVATE_PARENT")/clarifications"
python -m examples.seatlab.visibility.clarify_run seal "$packets" "$study" --start-utc "$START_UTC"
python -m examples.seatlab.visibility.clarify_run plan "$study"
# PAID: run only inside the corresponding window, with the same private spend directory.
keys run -- python -m examples.seatlab.visibility.clarify_run run-window "$packets" "$study" 0
# Offline: includes incomplete, invalid, and unattempted cells.
python -m examples.seatlab.visibility.clarify_report "$packets" "$study"
python -m examples.seatlab.visibility.spend report "$SEATLAB_SPEND_DIR" --run-prefix ask-
```

After each window stops, run `python -m examples.seatlab.visibility.clarify_meter "$study" --spend-dir "$SEATLAB_SPEND_DIR"` offline. It checks both turns against the original ledger and the sealed time boundaries. A late finish differs from a late start. A missing timestamp does not establish that a call never occurred. The meter does not authenticate the provider or verify an invoice.

## Can a sender use the right preference attached to the right record?

**Registered before any continuation call.** Enroll the 12 direct `brief` sources in the native briefing register, seeds `6501`–`6506`, repetitions `0` and `1`. Keep a source only if its native team passes the four-turn audit. Do not replace an absent source or select by score, brief accuracy, or opened reports. Preserve the accepted inventory reports, the actual six-letter card, and hashes for both upstream model turns. This is a scoped export of synthetic shipment data, not a general trace sanitizer.

Freeze those two upstream messages. Start independent inventory and dispatch calls in each of 3 policies. `aligned` releases the original card. `shifted` rotates the same 6 letters left by 1 slot. `repeat` sends the exact `aligned` prompt again with fresh model calls. Rotate policy order by `(seed − 6501 + repetition) mod 3`. Every sender can select 2 of the 6 revisions. Only the selected revisions enter the final dispatch prompt. Do not give the sender utilities or true final stock. Do not show either actor the treatment name or other draws. A repeated letter can survive rotation. Count these slots.

Run 36 continuations per transport. Use direct DeepSeek Flash and separate keyless `openai-codex/gpt-6.2-sol` programs. Do not pool transports. Each has at most 72 first attempts. There are 6 source worlds, not 36. Register failures as missing, without retries. Capture each direct call's cost in the existing results log. Do not rerun native teams. New decisions receive a stock score, not a new native Harbor reward.

Primary: the equal-world difference in optimal choices per 6 shipments, `aligned − shifted`. Within each world, pair by original source and average observed repetitions. Do not report a full-register estimate when a world has no matched repetition. Bound unknown scores over all 12 enrolled sources. Report `aligned − repeat` separately as variation under identical sender inputs. Also report agreement on opened report sets and final actions, changes in the best bay under the two reports, and coverage. These bounds are not confidence intervals.

### Start from the published team checkpoint

Use Python 3.12 or later from this branch. These commands need no Harbor, Docker, model account, or private trial archive. The second and third commands print the exact role prompts, not a description of them.

```sh
module=examples.seatlab.turn_fork
python -S -m "$module" plan
python -S -m "$module" show --source s6501-r0 --arm shifted
python -S -m "$module" show --source s6501-r0 --opened Q1 Q3
```

**To run fresh agents:** configure 2 trusted [seat programs](#run-your-own-reviewer-and-decision-maker). Set `reviewer` to the inventory program and `decider` to the dispatch program. The launcher passes each program its own prompt, not the world, answer key, other program's reply, or treatment label. Use a separate receipt directory for each role. Programs can incur costs. This interface does not isolate untrusted programs from your computer.

```sh
private=$(python -c 'from pathlib import Path; import tempfile; print(Path(tempfile.mkdtemp()).resolve())')
python -m "$module" seal --study "$private/team" --transport commands --config "$config"
python -m "$module" run --study "$private/team" --start 0 --stop 3
python -m "$module" pending --study "$private/team"
python -m "$module" report --study "$private/team" > "$private/report.json"
python -S -m "$module" verify "$private/report.json"
```

`$config` is the absolute path to your command configuration. One source has 3 consecutive cells. Give each worker a separate range of complete sources, such as `0:18` and `18:36`. The stop index is exclusive. At most 2 model turns run per cell. If work stops, `pending` lists only untouched cells. Preserve failed first attempts. The public report identifies separately the measured difference, disagreement with the same sender input, and bounds from missing first attempts.

**To export your own source:** `export --native-study DIR --native-report REPORT` reads an existing native briefing study with its original configurations. It emits only the accepted synthetic fields. Pin the exact bytes with SHA-256. Supply the pair to readers with `--packet PATH --sha256 DIGEST`. The direct transport accepts only the registered public packet; custom packets can use command programs. Do not publish raw private native folders.