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