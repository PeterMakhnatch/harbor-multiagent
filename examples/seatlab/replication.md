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

## Which teammate should receive a local tool?

**Registered before collection.** If only one teammate can request an exact list of its own free slots, should it be the sender or the receiver? The sender publishes a revised list. The receiver makes the final choice. This experiment reallocates one capability between roles. It does not give either seat the other calendar or a joint answer.

- Use the same 12 public scheduling worlds, both role directions, and 2 repetitions. For each of the 48 blocks, start 2 independent policies: `sender` and `receiver`. Each policy starts 2 stock Harbor rooms, `blind` and `peer`, sharing only that policy's 2 new opening replies. Do not reuse any calls or openings from the all-seats tool experiment.
- Both teammates receive the same tool offer and the same number of calls per action. Only the named role can obtain its own feasible starts on a valid request. This applies to its opening and later action. A valid request from the other seat returns `unavailable`. The model can decline. A malformed request stays invalid. The native world receives the agent's first final reply without repair.
- Start `sender` first if `(world // 2 + direction + repetition)` is even; otherwise start `receiver` first. Leave native room order unchanged. Use the registered direct Flash settings and stop rules. Register at most 192 teams, 96 matched comparisons, and 1,152 direct calls. Every started cell keeps its first attempt. Report missing or malformed openings separately from a verified native zero.
- Primary: native success under `receiver` minus `sender`, matched within world, direction, repetition, and room. Weight the 12 worlds equally. Report all registered lower and upper bounds. Show the room conditions, the 9 worlds outside the initial public cohort, and each role direction separately. Secondary: role-specific requests, exact self-reported lists, received tool results, accepted slots, and disagreement across repeats. Report costs separately from native scores.
- This measures which of 2 role assignments works better for this model and game. Both teammates' inputs can change downstream. It does not estimate the isolated benefit of a tool call. These are fresh model teams. A second program can replace either role without modifying the Harbor environment.

### Run a sender and a receiver from separate harnesses

Prepare the native schedule tasks as in [Run your own live team](#run-your-own-live-team). Create a new private study directory. Use two trusted executables with absolute paths. Each reads `{"schema":"seatlab.seat-turn/v1","prompt":"..."}` from standard input. Each writes `{"text":"..."}` to standard output. Do not print logs to standard output. Use a new conversation for each call.

```json
{
  "sender": {"argv": ["/absolute/path/to/sender"], "env": []},
  "receiver": {"argv": ["/absolute/path/to/receiver"], "env": []}
}
```

```bash
mkdir -m 700 "$private/role-teams"
"$python" -m "$module.calendar_tools_run" commands --design role-access --config seats.json \
  --tasks "$tasks" --study "$private/role-teams" --world 3 --direction 1 --repetition 1
"$python" -m "$module.calendar_tools_report" --design role-access "$private/role-teams"
```

`sender` and `receiver` are game roles, not fixed seat IDs. Their native seats reverse when `--direction` changes. Each program gets only its own turn. Configure credentials separately per executable, on the host. An empty `env` list passes no host credentials. These processes run as your user, not in a security sandbox. The external transport makes no billing claim. The study keeps verified Harbor results even when host usage is unknown.

## Can live teammates choose and use a tool that reads only their own calendar?

**Registered before collection.** Messages arrived in all 24 public replica teams, but none found a common start. A tool can reduce the work of reading a calendar without giving away the other seat's schedule. Test a complete team policy, not another replacement of a recorded list.

- Use all 12 [public scheduling worlds](results/flotilla-calendar-packet.json), both receiver directions, and 2 repetitions. Each of the 48 blocks receives both tool policies. Run a fresh native `blind` and `peer` room under each policy. Never compare `blind` alone against `peer` alone to estimate the tool effect. This registers 192 native teams and 96 matched team comparisons.
- Each policy starts with 2 new model-generated opening lists. Its 2 rooms share those lists, as in the original replica. Do not share model outputs or conversation memory between policies. Run `off` first when `(world // 2 + direction + repetition) % 2 == 0`; otherwise run `on` first. Keep the native room order unchanged.
- Each seat action gets 2 model calls with the same settings and output limit. The first call chooses `local_starts` or `none`. The offer and selection prompt are identical under both policies for identical inputs. Under `on`, a valid request returns every feasible 2-hour start for **that seat only**. Under `off`, the same request returns `unavailable`. The agent receives no other calendar, joint answer, recommended slot, or room control. A request with an argument, an unknown operation, or malformed JSON returns `invalid`. Do not retry it. A `none` request returns the same result under both policies.
- The second call receives the original role prompt and the result of the first call's request. Send its reply unchanged to the stock game. Do not sort, repair, or substitute model responses. The remote model sees no run IDs, policy names, task files, or provider credentials. Keep each model call stateless. Budget at most 1,152 direct first attempts. Compare actual usage and cost; equal call limits do not imply equal token counts.
- Primary: native success under `on` minus `off`, matched within world, direction, repetition, and room condition. Average differences within each world and weight the 12 worlds equally. Show `blind` and `peer`, and the 9 worlds outside the earlier public cohort, separately. Show counts, unknown-outcome bounds, and the accepted-decision comparison. A malformed final reply counts as zero only when the stock game records a verified zero. An invalid opening list starts no native room and cannot count as a native loss.
- Secondary: report requests, returned tools, exact lists produced by the agents, final slot choices, and both delivered messages. Separate native outcomes, evidence tying the tool to the seat, and model costs. A call without its receipt is not free. An observed difference estimates the entire policy, including tool failures and changed messages. It does not isolate a particular seat's reasoning.

### Run your own live team

Use this branch, Python 3.12 or later, and local Docker with Compose. Follow the [native setup](#try-a-native-team-without-an-api-key) through the `calendar_replica_source` check. Keep the resulting `$python`, `$tasks`, `$module`, and `$private` shell variables. The original 3-world replication remains a separate cohort. The single-block transport accepts all 12 pinned public worlds.

```bash
mkdir -m 700 "$private/tool-demo"
"$python" -m "$module.calendar_tools_run" demo --tasks "$tasks" --study "$private/tool-demo" \
  --world 5 --direction 0 --repetition 1
"$python" -m "$module.calendar_tools_report" "$private/tool-demo" > "$private/report.json"
```

This no-key demo selects the tool, sends empty lists, and picks slot 0. It starts 4 real Harbor teams. It tests the transport and verifier, not a model. For real model seats, use `calendar_tools_run.play_pair(tasks, study, block, respond)`. `tasks` and `study` are physical absolute `Path` objects. Create the private study directory first. `block` is a `calendar_protocol.Block(world, direction, repetition)`. `respond(prompt: str) -> str` gets only the current prompt. Start a new conversation for every call. Return the model's first text without repair. No provider SDK is required by the callback interface.

Assign different blocks to parallel workers. Each block claims its first attempt on disk. A second study cannot start the same native Compose project while it is active. Do not copy, resume, or replace a failed first attempt. The report keeps missing rooms unknown. To check costs from the first-party `direct` mode, pass `--spend-dir` to the report. External callbacks get native verification without a DeepSeek billing claim.

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

### Does sending two proposed slots improve a native schedule?

**Registered before any new model or native trial.** On three retained worlds, a scheduler selected a joint slot more often when it saw a real answer to two slot questions. That earlier test changed the last input to the receiver. This experiment changes something different: **whether the responder gets the scheduler's first proposals through the real Harbor mailbox**. The native task checks the submitted meeting. Use all 9 public worlds not used in the linked-role study: 01, 02, 03, 05, 06, 07, 09, 10, and 11. These worlds are held out from the linked-role study only, not from other SeatLab experiments or model training.

Use one pair per world and assignment direction, repetition 0. This registers 18 pairs, 36 native two-seat teams, and at most 108 first model calls. Both teams in a pair share the same actual first lists. Each seat sees only its own three private calendars. Ask each to propose up to two locally viable starts. Sol is the responder and Astra the final scheduler, regardless of native seat. The label and verifier answer never enter a model prompt. Use fresh subscription sessions and no paid keys. Do not filter proposals by their accuracy, length or overlap. Preserve every response, including invalid ones. Stop on a system or service identity failure; do not repeat an attempted block.

| Native step | No proposals sent (`blind`) | Proposals sent (`peer`) |
| --- | --- | --- |
| Scheduler → responder | The stock mailbox carries its ready marker. | The mailbox carries the scheduler's actual first list. |
| Responder → scheduler | See own private calendar and first list. Recommend up to two feasible starts. | Same instruction, plus the received list. Check suggested starts first; alternatives are allowed. |
| Scheduler → task | See own private calendar, own first list, and the actual responder recommendation. Submit one slot. | Same final instruction. The native verifier scores the submission. |

Use the stock counterbalanced arm order and both directions in every world. Make no replacement when a seed, arm, native reward, or service receipt is missing. Distinguish an unscored team from a verified zero. Primary: native success with proposals minus success without proposals. Average complete direction pairs within each world. Report this estimate only if every registered world has a pair. Also report worst/best bounds on the full register, numbers of improvements and harms, recommendation overlap, and query length adherence. The 18 pairs share 9 worlds. This is not a replication of the earlier +62.5-point role-input contrast: it tests a different message in a live system.

#### Run two native seat programs on your own machine

Use Python 3.12 or later, the fork's locked environment, and local Docker Compose. For a new run, [generate the public Flotilla tasks](#native-replication-with-an-external-harness) into a private directory. Generated tasks contain service credentials; do not commit or share them. A full study uses 18 paired blocks, but one block is enough to exercise your harness.

Each seat program receives one JSON line on standard input, with `schema: "seatlab.seat-turn/v1"` and one `prompt` string. Return one newline-terminated JSON object with a `text` string on standard output. The string is the model’s unchanged first reply. For example, `print(json.dumps({"text": reply}))`. Inside that string, lists use `{"starts":[...]}`. The final submission uses `{"slot":...}`. The host assigns the roles and sends the native messages. The seat does not need task files or Harbor credentials. Use an absolute executable path. The command config has exactly two roles:

```json
{"sender":{"argv":["/absolute/path/to/responder"],"env":[]},"receiver":{"argv":["/absolute/path/to/scheduler"],"env":[]}}
```

```sh
umask 077
study=$(realpath "$(mktemp -d)")
python -m examples.seatlab.visibility.calendar_query_run --tasks "$private/tasks/schedule" --study "$study" --commands /absolute/path/to/commands.json --world 1 --direction 0
python -m examples.seatlab.visibility.calendar_query_summary --study "$study"
```

The command claims a block before the first model turn. Different workers can run distinct world/direction pairs against the same study directory. Never submit the same block twice. A failed attempt stays in the register. Keep all raw native output private. Export the sanitized register with `calendar_query_summary --study "$study" --export > native.json`. Another worker can run `calendar_query_summary --input native.json` with standard Python only. The export contains decoded slot choices, original rewards and turn hashes. It contains no raw role prompts or task credentials.

**Export before you move the study.** The full native audit checks the original task and trial directory against the recorded configuration. A relocated private study can still supply the original team UUID and reward to stock Eval Lab. It cannot pass the full native audit at a different address. Never edit recorded paths to make it pass.

To check an exported submission away from the original machine, run `python3.12 -S -m examples.seatlab.visibility.calendar_query_summary --input native.json --verify-slots`. The check computes legal starts from both seats' public availability tables and compares each decoded submission with its recorded native reward. It cannot verify an undecoded submission or authenticate an unknown source archive. The service check binds model replies separately.

##### Split the native register between two workers

Give one worker direction 0 and one direction 1. Both use the same private study directory. Each worker takes only unstarted pairs from its own direction. Never queue an existing partial or invalid pair as a replacement. The register does not select worlds by reward. The original task runs at most one native team at a time in each worker. Apply the host-wide Docker limit if other projects also run trials.

```bash
worker=0  # use 1 in the second terminal
queue=$(mktemp)
python -m examples.seatlab.visibility.calendar_query_summary --study "$study" --export |
  jq -r --argjson d "$worker" '.rows[] | select(.direction == $d and .status == "not_started") | .world' > "$queue"
while read -r world; do
  python -m examples.seatlab.visibility.calendar_query_run --tasks "$private/tasks/schedule" --study "$study" --commands /absolute/path/to/commands.json --world "$world" --direction "$worker" || exit 1
done < "$queue"
rm "$queue"
```

##### Read the same native teams with stock Eval Lab

Eval Lab stays a consumer. Use a Python environment with Eval Lab installed. Point `$study` at your **private native run directory**, not the public result files. Use the archive exported from that directory. This reads every scored arm through `evallab.results.load_trial`. It checks the trial UUID and team reward against the archive. Unknown scores stay unknown. No API call, ingestion, catalog, or Eval Lab write is needed.

```bash
"$EVALLAB_PYTHON" -B - "$study" native.json <<'PY'
import hashlib, json, sys
from pathlib import Path
from evallab.results import load_trial

study, archive = map(Path, sys.argv[1:])
seen = set()
successes = 0
for row in json.loads(archive.read_text())["rows"]:
    key = "rx-w{world:02d}-d{direction}-r{repetition}".format(**row)
    for arm, cell in row["arms"].items():
        if cell["reward"] is None:
            continue
        trial = load_trial(study / key / "block" / "native" / f"{key}-{arm}")
        digest = hashlib.sha256(str(trial.id).encode()).hexdigest()
        if digest in seen or digest != cell["trial_id_sha256"] or trial.primary_reward != cell["reward"]:
            raise ValueError("trial identity or native team reward differs")
        seen.add(digest)
        successes += int(trial.primary_reward)
print(f"Matched {len(seen)} native team rewards; {successes} successes.")
PY
```

On the complete private register, stock Eval Lab printed `Matched 36 native team rewards; 25 successes.` These are team rewards, not 2 independent seat rewards per team. A standalone native trial need not have an Eval Lab job UUID.

### Can the receiver help its teammate recheck a calendar?

**Registered before the first calendar generation.** The 12 retained Flotilla `c1` talk worlds split 6 synthetic calendars evenly between 2 seats. Each world has exactly 1 common two-hour meeting. Earlier talk-versus-silent results do not say whether a receiver can help its sender correct a report. This study compares a peer-informed recheck with a blind recheck. It starts new teams; it does not replay the old free-text conversations.

Use all 12 frozen worlds, both assignments of sender and receiver, and 2 repetitions. In each of 48 blocks, both seats independently list the starts they consider locally feasible. Each sees only its own 3 calendars, the public rules, and the numbering of the 35 legal starts. Reuse these **same 2 actual lists** in both arms. Accept any well-formed list, including an empty or factually wrong one. Never supply a corrected list.

| Native step | Blind recheck | Peer-informed recheck |
| --- | --- | --- |
| Receiver → sender | Send a fixed ready message. | Send the receiver's actual initial list. |
| Sender → receiver | Recheck the whole local calendar. Send the revised list. | Same instruction. The sender can also see the receiver's list. |
| Receiver → world | Use its own calendar, its own initial list, and the delivered revision. Submit one start. | Identical inputs and instruction if the revised lists match. |

Use the unchanged talk task's authenticated mailbox and native verifier in both arms. The receiver alone submits. No model sees the other seat's raw calendar, hidden key, old action, old reward, or arm name. No tool removes a bad candidate or selects the model's final answer. A no-key transport control can compute a local list, but it is never a model trial. Independent starts and both native roles use the same first-party model, temperature, JSON constraints, and per-call budget limit.

Run repetition, world, then direction. Within a block, run `blind` first if their index sum is even; otherwise run `peer` first. Register at most 96 initial generations, 96 fresh native teams, and 192 native generations. Each native team sends 2 messages and makes at most 1 final submission. Use a new private directory for each first attempt. Do not retry or replace a sample. Stop on a transport, identity, funding, or evidence failure. Distinguish an invalid model response, an unattempted branch, and a native loss. Keep settled costs for incomplete trials.

Primary: the paired change in native verifier success, `peer − blind`. Average complete contrasts within a world, then weight the 12 worlds equally. Withhold the full-panel estimate if any world lacks a complete pair. Report coverage, each arm, direction, and repetition. Secondary: whether a sender reports the true start, reports its exact local set, or changes its list after the recheck. When both arms deliver the same revised list, report how often the receiver changes its answer despite identical input. Twelve synthetic worlds and one provider bound generalization. A successful submission does not prove that either model computed the full calendar correctly.

An invalid model action can count as a native zero only when the retained response fails the grammar and the stock verifier confirms that no submission succeeded. Report this case separately. An absent model response or native reward is unknown, not zero. Alongside the primary result, report accepted-decision pairs and bounds that give each unknown binary reward its full range from 0 to 1.

**Try one seat with your own harness.** Use Python 3.11 or newer from the repository root. These commands need no Harbor install, Docker, provider, or private archive.

```bash
packet=examples.seatlab.visibility.calendar_packet
protocol=examples.seatlab.visibility.calendar_protocol
python -m "$packet" verify
python -m "$protocol" prompt initial --world 0 --seat p1 > p1-user.txt
python -m "$protocol" prompt initial --world 0 --seat p2 > p2-user.txt
```

Send each file to a separate model context. Save each response as `p1.json` or `p2.json`. Never give a model the combined packet. For the peer arm, use the real `p2` response below. For the blind arm, replace `@p2.json` with `'{"ready":true}'`. Keep both initial responses fixed.

```bash
python -m "$protocol" prompt recheck --world 0 --seat p1 --own @p1.json --peer @p2.json > recheck.txt
# Send recheck.txt to the sender. Save its response as revised.json.
python -m "$protocol" prompt final --world 0 --seat p2 --own @p2.json --sender @revised.json > decide.txt
# Send decide.txt to the receiver. Save its response as decision.json.
python -m "$protocol" decode slot @decision.json
```

`decode` translates the model's slot into a weekday and start time. It does not fix an invalid choice or assign a Harbor reward.

**Rebuild the inputs from public source.** Use a checkout of the [fork's Flotilla generator at `71ddf40b`](https://github.com/PeterMakhnatch/harbor-multiagent/tree/71ddf40b14ea81dd8eb0c32663e11abfe21cd1e3/flotilla). Keep the generated tasks private. They contain fresh service credentials.

```bash
umask 077
private_tasks=$(mktemp -d)
python "$GENERATOR_CHECKOUT/flotilla/build_tasks.py" --family schedule --instances 0-11 \
  --seats 2 --difficulty easy --seed 1 --conditions talk --play-timeout 600 --out "$private_tasks"
python -m "$packet" compare-tables "$private_tasks/schedule"
```

We independently generated all 12 worlds. All 24 role prompts matched byte for byte. The fresh credentials change the native task hashes. `compare-tables` checks the model inputs, not task identity. The registered native experiment requires the original task hashes. Its [public packet](results/flotilla-calendar-packet.json) has SHA-256 `b7073bdb5c2bdd44e6e6b2445137b08d3856db6793bdf8d17ba2a0eb7af86de0`.

#### Native replication with an external harness

The archived task hashes above cannot be recreated: each build makes fresh service credentials. This independent replication uses the public Flotilla generator and freshly built tasks. It does not add replacements to the original panel.

Before any new model turn, fix worlds 00, 04, and 08, both role assignments, and 2 repetitions. Use 12 paired blocks, up to 24 fresh native teams and 72 first-party model calls. Run repetition, world, then direction. Within a block, use the original arm order and the same actual initial lists. Pass only the current seat's prompt to the external model. Do not pass the task, other seat's table, identifiers, arm label, verifier, or previous chat. No retries or corrected responses. Stop on an infrastructure failure. Keep an invalid model action distinct from a missing reward.

The primary measurement is the native success difference, peer-informed minus blind, within each complete block. Average within world first. Withhold the 3-world estimate if any enrolled world has no pair. Report coverage, stock verifier scores, message delivery, prompt equality, and metered calls separately. This checks whether a third-party harness can run the same intervention on newly generated tasks. These 3 worlds are not held-out tasks, and this is not an independent model comparison.

##### Try a native team without an API key

Use a full checkout of this fork's `experiments/seatlab-calendar-public` branch, Python 3.12 or later, and local Docker with Compose. Keep the task and trial outputs private. Generated task files contain service credentials. The trial output contains role messages.

```bash
uv sync --locked
python=.venv/bin/python
umask 077
private=$(realpath "$(mktemp -d)")
generator=71ddf40b14ea81dd8eb0c32663e11abfe21cd1e3
git fetch https://github.com/PeterMakhnatch/harbor-multiagent.git "$generator"
git worktree add --detach "$private/generator" "$generator"
"$python" "$private/generator/flotilla/build_tasks.py" --family schedule --instances 0-11 \
  --seats 2 --difficulty easy --seed 1 --conditions talk --play-timeout 600 --out "$private/tasks"
tasks="$private/tasks/schedule"
module=examples.seatlab.visibility
"$python" -m "$module.calendar_replica_source" --tasks "$tasks" --world 0
"$python" -m "$module.calendar_client" demo --tasks "$tasks" --out "$private/demo" \
  --world 0 --direction 0 --repetition 0
"$python" -m "$module.calendar_replica_report" "$private/demo"
```

The demo sends empty availability lists and selects slot 0. It tests the harness, not a model. On our fresh world 00, the replay verified 6 callback turns and 2 native scores of 0. Both receivers saw identical final text and chose the same slot. Native token counts were null, not zero.

##### Connect a model or another harness

Call `play(tasks, out, block, respond)` from your host. `respond` must take 1 prompt string and return 1 response string. For example, this adapter calls an existing executable that reads one prompt from standard input:

```python
import subprocess
from pathlib import Path
from examples.seatlab.visibility.calendar_client import play
from examples.seatlab.visibility.calendar_protocol import Block

def respond(prompt: str) -> str:
    return subprocess.run(["my-seat-model"], input=prompt, text=True,
                          capture_output=True, check=True, timeout=100).stdout

# tasks is the generated schedule directory. out is a new physical path
# inside a private directory, outside the checkout.
status = play(Path(tasks), Path(out), Block(world=0, direction=0, repetition=0), respond)
```

Use a new model context on each call. Do not add the block ID, arm name, other seat's table, or task path. A callback error stops the block. The harness forwards a grammar-invalid answer without a retry. The stock verifier scores the team's action; the relay never assigns a reward. Account for model calls in the external process. Report missing evidence separately from a native score of 0.

Eval Lab can read each ordinary trial without importing it into a database. In an environment with the existing Eval Lab reader, run the following after the demo:

```bash
python -B - "$private/demo/native" <<'PY'
import sys
from pathlib import Path
from evallab.results import load_trial

for path in sorted(Path(sys.argv[1]).iterdir()):
    trial = load_trial(path)
    print(path.name, trial.primary_reward, len(trial.result["seat_results"]))
PY
```

On the no-key demo, the stock reader returned both scores and all 4 seat records. The input, cache, and output token counts were null in all 4 records. Use external receipts for model usage. Do not replace those null values in the Harbor records.

##### Replicate the direct model cohort

**Paid:** run the following only after you authorize a budget. Keep the same initialized `SEATLAB_SPEND_DIR` for the entire study. The [budget instructions](#run-a-new-blinded-listener-experiment) describe initialization. Do not reuse `rx-` identifiers from an earlier study or reset its ledger. The direct adapter makes at most 6 calls per block. It passes no provider key to Harbor.

```bash
study="$private/direct"
id=rx-w00-d0-r0
mkdir -m 700 "$study" "$study/$id"
keys run -- "$python" -m "$module.calendar_client" direct --tasks "$tasks" \
  --out "$study/$id/block" --meter "$study/$id/meter" \
  --world 0 --direction 0 --repetition 0
"$python" -m "$module.calendar_replica_report" "$study/$id/block"
"$python" -m "$module.spend" report "$SEATLAB_SPEND_DIR" --run-prefix rx-
```

The direct adapter seals an external receipt for each first attempt. A Harbor turn receipt shows which text crossed the process boundary. The external receipt adds the served model and its token counts. The ledger records estimates for settled calls and a hold for unresolved calls. Matching those records does not verify the provider's invoice.

To summarize your 12 paired blocks offline, run both readers:

```bash
"$python" -m "$module.calendar_replica_series" "$study"
"$python" -m "$module.calendar_replica_meter" "$study" --spend-dir "$SEATLAB_SPEND_DIR"
```

The first command works for any harness. It compares only scores from matched native trials. It retains each missing slot and counts identical final inputs separately. The second command checks optional direct-adapter costs. Missing cost records do not erase a verified native outcome.

##### The real model changed its choices, but neither team policy won

All 12 registered pairs finished. The stock verifier scored all 24 model teams. The blind teams won **0 of 12** games. The peer-informed teams also won **0 of 12**. Every final response selected a legal slot. The equal-world paired difference was **0 percentage points**.

The receiver's final input differed in all 12 pairs. Its chosen slot differed in 11. The native timelines verified all 48 game-message deliveries. The result is therefore not a missing score or an undelivered-message result. It also does not show that feedback has no effect in other scheduling games.

The [paired outcomes](results/flotilla-calendar-external-results.json) identify the 24 distinct native trials by digest. Eval Lab independently read the same 24 scores and 48 seat records. All native token counts remained unknown. The [external meter](results/flotilla-calendar-external-meter.json) matched all 72 observed turns to settled direct calls, with no missing record or hold. The [estimated cost](results/flotilla-calendar-external-cost.json) was **$0.003356052 USD**: 24,184 cached input tokens, 15,486 uncached input tokens, and 1,601 output tokens. These are local list-price estimates, not an invoice.

##### Is the failure in the lists or the final decision?

**Registered before any new receiver generation.** In the 24 native games above, the common start was missing from at least one final list in 20 games. In the other 4, both lists contained the correct start. All 4 receivers chose a different start that neither calendar allowed. This observation motivates a *conditional replay* of the final decision. It does not change a historical Harbor score.

Use all 24 published final inputs: worlds 0, 4, and 8; both receiver directions; both original repetitions; and both original message conditions. Preserve the receiver's calendar and the full original final instruction. Independently keep or replace each of the 2 lists. A replacement contains the exact feasible starts for **only that seat**, computed from its own calendar. Never give the receiver the other seat's calendar, the intersection, a recommended slot, or the original decision. Reconstruct the unchanged prompt byte for byte against the published hash. A replaced list is a counterfactual input, not a claim about what the original agent said.

| Condition | Receiver list | Sender list |
| --- | --- | --- |
| `rr` | As reported | As reported |
| `er` | Exact local starts | As reported |
| `re` | As reported | Exact local starts |
| `ee` | Exact local starts | Exact local starts |

Take 2 fresh draws per input and condition with the same direct model. That gives 48 matched sets of 4 decisions and at most 192 first attempts. Do not retry, filter by original failure reason, or stop when a model wins. Use `rr er ee re`, `er re rr ee`, `re ee er rr`, and `ee rr re er` as the 4 condition orders. Select order `(case_index + draw) mod 4`. Sort cases by world, direction, original repetition, and original condition (`blind` before `peer`). Reverse case iteration in the second draw. Keep the original index for choosing the order.

Primary: compare `ee − rr` within a case and draw. Measure whether the model's actual slot fits both seats. Average matched differences within each world, then weight the 3 worlds equally. Report the 2 one-list effects, the complete-set interaction, and disagreement across identical-prompt draws. Count an observed invalid reply as protocol failure, separately from a missing reply. Show accepted-only results and bounds for missing replies. Report the 3 worlds separately. The 192 scheduled calls are not 192 independent worlds.

This isolates the final receiver. No sender reacts to a replacement. The offline score checks the same two-hour constraint as the native game, but it is **not** a new native trial or verifier reward.

**Try the replay without Harbor, Docker, or a provider.** Run these commands from the repository root with Python 3.11 or newer:

```bash
python -m examples.seatlab.visibility.calendar_list_packet verify
python -m examples.seatlab.visibility.calendar_list_protocol prompt rx-w00-d0-r0-blind er
python -m examples.seatlab.visibility.calendar_list_protocol score rx-w00-d0-r0-blind '{"slot":11}'
```

The [public packet](results/flotilla-calendar-list-packet.json) binds the 24 recorded inputs to the original native trials. Its SHA-256 is `06ec8c6bceb14db004bc874c18709a7e2ce92837b653b1ba6ce45da23207d322`. The first command reconstructs all 24 prompts. The second prints exactly one role view. The third checks the reply, not the input list.

To compare your own model, pass a function of one prompt to `calendar_list_run.collect(study, run, respond)`. The callback returns one raw string. Use `calendar_list_protocol plan` to list the registered run IDs. Give each worker different IDs and the same private study directory. A failed attempt remains claimed; another worker cannot accidentally buy a second reply. This protects the experiment from duplicate work. It is not a sandbox for an untrusted callback.

```bash
study="$(mktemp -d "$(realpath "${TMPDIR:-/tmp}")/calendar-list.XXXXXX")"
# Free transport check. Use a different, empty study directory for real replies.
python -m examples.seatlab.visibility.calendar_list_run demo "$study" --draw 0
python -m examples.seatlab.visibility.calendar_list_report "$study"
```

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