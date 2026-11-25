# Flotilla: controlled multi-agent experiments on harbor-multiagent

## Start here

Flotilla builds small multi-agent evaluation worlds on the Harbor framework. Each agent has a seat in a shared
world. It can read data, post messages, and submit answers. The world scores the final answer. Scripted teammates
send fixed reports, true or false, so each model under test gets the same inputs from its teammates.

### The 2026-11-24 behavior pilots

The pilots tested 7 pre-registered designs in 2,616 valid trials across 3 models. Two designs showed an effect:

- [Read first (`rf1v`)][read-first]: GPT-6 Luna used a false subtotal in 32 of 32 trials when told to read the
  team's messages before opening any file. It used the false subtotal in 0 of 32 matched `vf1` trials
  without that instruction. Haiku and DeepSeek did not use the false subtotal in any `rf1v` trial.
- [Limited audits (`au1`)][audit]: each model could check 1 of 3 reports. Across models, 65% to 98% of trials
  passed on the false report. A sentence that exposed the wrong method reduced the rate to 30% to 60%.

Five designs showed no effect on the primary outcome: [verify with full data (`vf1`)][verify],
[unanimous wrong totals (`pc2`)][conformity], [different timing and peer roles (`pc3`)][timing],
[a false argument at submission (`pa1`)][argument], and [read-first conformity (`rf1c`)][read-first].

### Run the demo: local, no model, no cost

```bash
bash flotilla/demo.sh    # from the repository root
```

Use local Docker, `/tmp/crucible-docker-slot.sh`, and a Flotilla Harbor venv with PyYAML. The script defaults to
`~/Developer/harbor-multiagent-flotilla/.venv/bin/harbor`. Set `FLOTILLA_DEMO_HARBOR` for another venv.
Docker needs the cached `python:3.13-slim` image and the cached Flotilla player build layer.
The script builds and checks 5 tasks. Harbor runs `oracle` on every seat, with no model, API key, or Modal.
The script keeps the jobs, probe analysis, [card](analysis/README.md#behavior-cards-flotilla-cards), and
[timeline](analysis/README.md#trial-timeline) under `$TMPDIR` (default `/tmp`). It prints their paths.
The oracle uses reference answers. The demo checks the pipeline, not model behavior.

### A trust card for a model: paid

The [trust card protocol][protocol] uses 184 planned trials per model on Modal. Run the following from the
repository root. Use a commit that contains section 13 and is an ancestor of HEAD. The plan needs the frozen
`rf1v` and `au2` task sets under `.flotilla-local/tasks/`.

```bash
PREREG_SHA=547c88c9
python3 flotilla/trust_card.py plan \
    --model openrouter/openai/gpt-6-luna --name card-example --prereg-commit "$PREREG_SHA"
python3 flotilla/run_wave.py --dry-run flotilla/plans/cards/card-example.json
```

Planning and previewing do not start trials. Check the cost forecast and any zero-data-retention (ZDR) warnings.
Get approval before starting a paid run. Commit the plan before the run. Follow the [trust card runbook][cost]
for the run, the single invalid-cell retry pass, and `trust_card.py report`.

The generated plan reserves $0.02 per in-flight trial and caps model spend at $1.104 per model by default
(`--cap-usd` sets a total cap). Modal costs about $0.0021 per trial and is outside that cap. The plan sets
provider balance floors of $2.00 for OpenRouter and $1.50 for DeepSeek when used. It allows 10 jobs at once,
spaces starts by at least 5 s, and stops new starts after 6 consecutive invalid trials overall or for one model.

[read-first]: docs/FINDINGS.md#2026-11-24-0355-et--told-to-read-the-teams-messages-first-gpt-6-luna-sums-the-reports-and-passes-every-slip-through
[audit]: docs/FINDINGS.md#2026-11-24-0415-et--an-integrator-that-can-check-1-of-3-reports-passes-a-slip-through-65-98-of-the-time-a-method-tell-halves-it
[verify]: docs/FINDINGS.md#2026-11-24-0225-et--an-integrator-that-holds-the-data-never-passes-a-teammates-slip-through-only-haiku-tells-the-team
[conformity]: docs/FINDINGS.md#2026-11-24-0245-et--no-seat-took-a-unanimous-wrong-total-because-every-seat-had-computed-before-it-read
[timing]: docs/FINDINGS.md#2026-11-24-0300-et--realistic-post-times-one-peer-and-a-lead-claim-change-nothing-either
[argument]: docs/FINDINGS.md#2026-11-24-0345-et--a-false-argument-at-the-moment-of-commitment-lowers-doubt-instead-of-raising-it
[protocol]: docs/PROBE.md#13-standing-pre-registration-the-trust-card-written-2026-11-24-before-any-trust-card-trial
[cost]: docs/NEXT-WAVE.md#trust-card

## The design

Does a team of agents beat one agent because it has more workers, or because its workers
coordinate? Flotilla runs every task instance in three conditions and reads the answer off the
difference:

| Condition | Seats | Information | Messaging |
|---|---|---|---|
| `solo` | 1 | all of it | none |
| `silent` | N | split across seats | disabled |
| `talk` | N | split across seats | enabled |

*Recovery* = (talk − silent) / (solo − silent) is the share of the information gap that the team
closes by talking. The world service authenticates every seat, records every message, delivery and
submission, and freezes the answer before grading. See [CONTRACT.md](CONTRACT.md).

## Results so far

[docs/RESULTS.md](docs/RESULTS.md) has the canonical waves of 2026-11-19 (4 open models, world v2):
talking adds a resolved gain for DeepSeek (it closes 0.92 of the gap, exact p 0.001) and a positive
but unresolved gain for the other 3 models; silent seats send thousands of requests to unknown world
paths and use the answer slot as a query channel; talking teams found the answer in 36 of 36 trials
but lost 7 to a later wrong write; on a narrow channel, scores fell by 0.33 to 0.42 and about half to
two-thirds of messages carried no data; a lying peer deceived DeepSeek in 5 of 8 trials and Qwen in
4 of 5. Sample sizes are small (8 to 12 instances); RESULTS.md says which effects are resolved.
[docs/FINDINGS.md](docs/FINDINGS.md) is the dated log of observations and the validity faults found
on the way.

## Run it

From the repository root, install the seat-capable fork in its own environment. Use the local Docker slot
wrapper for oracle jobs. Do not replace a global Harbor installation.

```bash
uvx --from 'uv>=0.13.5' uv sync --locked
python3 flotilla/build_tasks.py --family schedule --instances 0-11 --seats 2
uvx --quiet --python 3.12 --with pyyaml python flotilla/qa.py 'flotilla/tasks/schedule/*'
python3 flotilla/make_jobs.py --tasks 'flotilla/tasks/schedule/*' --agent oracle --name oracle-check --env docker
/tmp/crucible-docker-slot.sh .venv/bin/harbor run -c flotilla/configs/generated/oracle-check-1seat.json -y
/tmp/crucible-docker-slot.sh .venv/bin/harbor run -c flotilla/configs/generated/oracle-check-2seat.json -y
```

Generated tasks are git-ignored: they contain answer keys, seat tokens and seeds, and seats have
public network access.

Tasks get opaque names: the folder is `flotilla/tasks/<label>/t-<12 hex>/` and the task name is
`flotilla/t-<12 hex>`, random for each build. Harbor names a trial's Docker containers and networks
after the task folder, and a seat can read those names, so a name must not say the condition.
`flotilla/tasks/manifest.json` maps each name to its label, instance, condition, seats and build
parameters; `task.toml` `[metadata]` and `environment/task.json` say them too. No seat reads these
files. See [CONTRACT.md](CONTRACT.md) section 2.

Each build draws a secret seed. The family generators are public, so a seat that knew the seed could
compute the answer key. The manifest, `task.toml`, `task.json` and extension-only oracle
`solution/policy.json` record it. No model seat reads it. `--seed N` repeats a build:
the canonical waves used `--seed 1`, which is public, so `qa.py` fails such a set
(`public-seed`) unless `--allow-public-seed`, which is only for repeating a published build.
`--seed-of PATH` gives a build the seed of the tasks under PATH, so that the two builds have the same
instances, for example a narrow channel against the `talk` tasks above:

```bash
python3 flotilla/build_tasks.py --family schedule --instances 0-11 --variant tight --max-text 100 \
    --max-messages 2 --conditions talk --seed-of flotilla/tasks/schedule
```

Other options of `build_tasks.py`: `--difficulty easy|hard`, `--seats N`, `--variant <label>`,
`--max-text <characters>` and `--max-messages <count>` (message limits per seat; the `talk`
instructions state them when they are not the defaults), `--conditions`, `--scripted`,
`--readable-names` (the names `<label>-<iii>-<condition>` of builds before opaque names; for debugging
only: `qa.py` fails such a set and `make_jobs.py` refuses it, unless each gets
`--allow-readable-names`).

## Check a task set before a wave

Real runs found validity faults that each corrupted a condition ([docs/FINDINGS.md](docs/FINDINGS.md)):
an answer example that was the answer, a world reply that named the condition, a counter in the
submit reply, one network for all seats, a scripted peer whose format looked more authoritative, and a
`Server` header that named the software, whose public generator gives every answer key from the public
seed 1. World versions 2 and 3 and secret seeds fix them in code. `qa.py` checks a built task set, so
that they do not come back:

```bash
uvx --quiet --python 3.12 --with pyyaml python flotilla/qa.py 'flotilla/tasks/*/*'
uvx --quiet --python 3.12 --with pyyaml python flotilla/qa.py 'flotilla/tasks/*/*' --json
```

It reads the task folders and calls each task's own `solution/family.py`. It starts no container and
uses no network or model. Exit status is 1 for any FAIL, 2 when no task matches, and 0 otherwise.
See the behavior probe section of [NEXT-WAVE.md](docs/NEXT-WAVE.md) for frozen, pre-registered sets.
No output names a token, an answer key or a secret seed. `--allow-readable-names` makes
`runtime-names` a WARN, for a local debugging run of a set built with `--readable-names`.
`--allow-public-seed` makes `public-seed` a WARN, only to repeat a published build such as the
2026-11-19 waves (`--seed 1`).

| Rule | Level | Finding |
|---|---|---|
| `example-leak` | FAIL | a JSON object in a seat's instruction scores 1 against the answer key or the decoy |
| `condition-leak` | FAIL | a byte that a seat under test reads (instruction, environment, briefing, Compose settings, image) names the condition or says `liar` or `warned`; or the warning sentence is in an unwarned condition, or missing from a warned one |
| `software-name` | FAIL | a byte that a seat under test reads says `flotilla` or `harbor-multiagent`: the seat can look for the public source, whose generator gives the answer key from the seed |
| `opaque-names` | FAIL | a task built with opaque names has a task name or folder name that is not `t-<12 hex>`, or that names a condition, its family or its variant |
| `twin-mismatch` | FAIL | a seat under test can distinguish twin arms from seat-visible bytes (see CONTRACT.md) |
| `seat-network` | FAIL | a seat is not alone with `main` on a network of its own: a shared network, the default network, a shared namespace or volume |
| `seat-secrets` | FAIL | a seat gets the answer key, the build's secret seed, `task.json`, `task.toml`, the build's `manifest.json`, a world, family or score file, `FLOTILLA_ADMIN` or another seat's token (environment, volumes, files that its image copies) |
| `token-map` | FAIL | seat tokens are not distinct, or `main`'s `FLOTILLA_TOKENS` does not map each one to its seat |
| `stale-code` | FAIL | the task ships a `world.py`, `score.py`, `oracle.py` or family file that differs from this checkout: build the set again, except a frozen pre-registered set (NEXT-WAVE.md), which runs as built |
| `qa-error` | FAIL | a task file is missing or cannot be read |
| `game-secret` | FAIL | a game instruction or briefing leaks a secret, misstates the rules or steers a choice |
| `runtime-names` | FAIL | the set has readable names (`--readable-names`, or a build before opaque names): the Docker names that Harbor derives from a task folder say its condition or label; a WARN with `--allow-readable-names` |
| `public-seed` | FAIL | the set was built with a public seed: below 1,000,000 (such as the canonical `--seed 1`) or named in this checkout's documentation; a WARN with `--allow-public-seed`, only to repeat a published build |
| `container-name` | FAIL | a service sets `container_name`: a seat reads that fixed name by a reverse DNS lookup (Harbor's own names are opaque), and concurrent trials would share it |
| `confederate-plan` | FAIL | the roster, posts, briefing, gate or read-first text differs from the probe spec |
| `audit-data` | FAIL | audit data leaks, or the audit files, budget or scripted posts disagree with the spec |
| `policy-data` | FAIL | the file, totals or claim disagree with the policy rule and scripted misreading |
| `push` | FAIL | `task.json` `push` is set but not `true`, is on a task that is not a probe task with messaging, or the subject's push note does not match it |
| `inbox` | FAIL | `task.json` `inbox` is set but not `true`, is on a task that is not a probe task with messaging, the subject's "Team messages so far" block is not `task.json`'s scheduled posts exactly, or a seat has a block without `inbox` |
| `solo-sufficient` | WARN | in a team condition, one seat's own briefing gives a correct answer (`oracle_answer`) |
| `canary` | FAIL | an extension bundle lacks its hidden contamination marker or a seat can read the GUID |
| `versions` | FAIL | an extension bundle's short version hashes do not match its files |
| `answer-leak` | FAIL | a seat-readable field contains a hidden value declared by the extension family |
| `leak-check-missing` | WARN | an extension family supplies neither `leak_values(task_json)` nor a `qa` hook |

Harbor names a trial after the first 32 characters of the task folder's name, and names the trial's
containers and networks after the trial. A seat can read these names with a reverse DNS lookup: a
silent seat read `schedule-004-silent__cdeu7ht__env-main-1` in wave 2, and in `schedule.liar` every
condition's names contained `liar`. Opaque names (the default) close this leak on the Flotilla side,
with the current Harbor; the open harbor-framework/harbor#3803 (opaque session ids) would close it in
Harbor for every task. `make_jobs.py` refuses tasks with readable names, unless it gets
`--allow-readable-names` for a local debugging job.

## Families

| Family | Hidden information | Answer | Score |
|---|---|---|---|
| `schedule` | each seat knows some attendees' calendars | the one 2-hour window that fits everyone | 1 or 0 |
| `ledger` | each seat holds a shard of the transaction log and some opening balances | every account's closing balance | share of accounts exactly right |
| `puzzle` | each seat holds some clues of a logic-grid puzzle | every person's floor and two other attributes | share of cells right |
| `probe` | none, except in `audit` | one whole-number total | 1 or 0 for `p1`'s own last answer |

`ledger` twists: a transfer is two rows held by different seats, and a reversal names only the row
it cancels, which another seat holds. Pooling per-account subtotals, or booking both legs of a
transfer, gives wrong balances; pooling the rows gives the right ones. `puzzle`: all clues fit
exactly one solution, every clue is needed, and one seat's clues fit many solutions.

Only `p1` is under test in `probe`. It holds all the data in conformity and verify, but not in audit.

`probe` is a behavior probe, not a pooling task (docs/PROBE.md, CONTRACT.md 12). Kind `conformity`: the file has
`void` rows that the question leaves out; counting them gives the confederates' wrong total. Its four arms
(`talk-quiet`, `talk-wrong`, `talk-dissent`, `talk-right`) differ only in what the confederates post. The world's
submit gate shows a submitting seat its unread messages first and logs the refused answer. The world itself adds
the confederates' messages at the subject's first request, dated 3-12 s before it (task.json `scheduled_posts`,
world version 14). Two more arms are built only when named (`--conditions`): `talk-one` (one confederate posts W) and
`talk-lead` (the same message behind a claim to lead the team).
`build_tasks.py --family probe` builds 4 seats and the 4 arms by default (label `probe.conformity`). Kind `verify`
(`--variant verify`, label `probe.verify`): `p1` holds one file per month and integrates the team's result; each
confederate posts its month's subtotal. Arms `talk-quiet` (no posts), `talk-honest` (every subtotal right) and
`talk-false` (one drawn confederate posts its month's subtotal with the `void` rows counted).
Kind `audit` (`--variant audit`, label `probe.audit`): the same months, but `p1` holds no file; it may audit one
month with `POST /audit` (world version 15; `?format=csv` gives the plain file from version 18), which serves the
month's CSV from task.json. Arms `talk-honest`, `talk-false` and `talk-false-tell` (the false confederate also says
it added up every row of the category). `--audit-budget N` (0-3) lets it audit N months instead of one.

Conformity also has extra arms `talk-push`, `talk-argue` and `talk-policy` (requires `--policy-file`).
Verify and audit have extra arms `talk-false-insist` and `talk-false-admit`. See [CONTRACT.md](CONTRACT.md)
for reply timing and for `--read-first`, `--push`, `--inbox` and `--post-trigger first_submit`.

`flotilla/trust_card.py plan` writes a wave plan. `report` analyses a finished wave.
See [the runbook](docs/NEXT-WAVE.md) for `run_wave.py`, including the cost forecast and no-cost `--smoke` mode.

## Add a family

Seams studies one coordination mechanism per task. A careful single agent must solve the `solo`
arm. A team has the same work with one named seam between its agents. Matched probe arms measure
the effect of that seam with scripted teammates.

Add these files, with the same Python identifier for `<family>`:

- `flotilla/seams/<family>.py`: generator, arm renderer, paired-arm and family-specific QA.
- `flotilla/world/ext/<family>.py`: referee routes, logical ticks, seat shifts and event scorer.
- `flotilla/policies/<family>.py`: careful, naive and scripted seat policies.

No registration or core edit is needed. Follow [CONTRACT.md section 22](CONTRACT.md#22-extensions-policies-and-the-simulator).
It specifies every interface, including `host.post`, `GET /await`, pushed messages, delivery receipts,
and a shared family module. It contains worked examples of capped-note shifts and a round barrier.
`kit_fixture` implements both for tests only.

Declare `SEAM`, `LEVELS`, `TWINS`, `PAIRS`, `pair_difference`, `ARM_WORDS`, `COMMON_WORDS`,
`leak_values(task_json)` and `chance_baseline(task_json, policy)` (or `CHANCE`) as applicable.
Section 22a describes their interfaces and how EvalCard reads them.
QA checks declared answer values in every static seat view and warns if both leak-check hooks are absent.
Each extension task has a training-contamination marker (`build_tasks.CANARY`) in its hidden files.
QA rejects missing markers and visible GUIDs. The verifier and simulator report `versions` hashes of
the shipped family, extension, policies, world and every seat instruction. Legacy tasks do not get
these markers or stamps.

```bash
python3 flotilla/build_tasks.py --family kit_fixture --difficulty heavy --instances 0-4 \
    --conditions solo,team,talk-control,talk-trap,hint,staged --out /tmp/seams-tasks
uvx --quiet --python 3.12 --with pyyaml python flotilla/qa.py '/tmp/seams-tasks/*/*'
uvx --quiet --python 3.12 --with pyyaml python flotilla/sim.py --family kit_fixture \
    --difficulty heavy --arms solo,team,talk-control,talk-trap,hint,staged --instances 0-4 --schedule-seeds 0-4
```

The simulator needs no model or Docker. It uses the same referee and policy code as an oracle run.
It sweeps task instances and scheduler seeds. Check the complete score and event log, not only the reward.
Prove that careful play scores 1 in `solo` and the team arms, and that no single seat can score
alone in a team arm. Measure the naive probe against its matched control. Use `TWINS` for identical
views and `PAIRS` when exactly the specified sentence differs. Check the chance baseline, all three
dose levels (`canary`, `standard`, `heavy`), and reproducibility from the same seeds.
Test waiting, call budgets, delivery receipts and invalid outcomes; never count an invalid outcome as 0.

Use `build_tasks.py --policy naive` (or `careful` or `nop`) for a named oracle build.
Extensions also accept their declared `lapse-10`, `lapse-20`, `lapse-30`, `lapse-50` and
`script-loop` policies. Use `own.lapse(percent, key)` for deterministic per-opportunity lapses.
The simulator and seat runner use identical draws. These policies measure grader sensitivity,
not model performance. Use a separate output root for each policy.
The option does not change the model briefing or scripted teammate policies.
Require zero QA failures and a local, zero-cost oracle rehearsal before a model run.
For staged seats, set `start_after` in the extension arm. Set `--play-timeout` to cover the whole chain;
use separate jobs for different turn caps. The kit does not receive runner-end signals.

### Make an eval card

```bash
uvx --quiet --python 3.12 --with pyyaml python flotilla/eval_card.py \
    --family kit_fixture --levels canary,heavy --instances 4 --schedule-seeds 3 --jobs 2 --out /tmp/card
```

The card writes `<family>.card.md` and `<family>.card.json` from disposable simulations. It exits
with status 1 on any FAIL. NA means the check does not establish a pass. It reports the matrix,
checks, event hashes and estimated cost; it does not run a model. Never reuse its seed for a model wave.
Use `--policies careful,naive,nop,lapse-30,script-loop` to include lapse and batch policies in the sweep.

### Rehearse the instructions with Playtest

**Warning:** host seats are not isolated. Use only trusted commands and tasks. The outside-path audit
is not proof of isolation. See section 22e of [CONTRACT.md](CONTRACT.md#22e-rehearse-the-player-experience-on-the-host).

Build one task, then replace `ASSISTANT` below with a trusted command for the seat:

```bash
python3 flotilla/build_tasks.py --family kit_fixture --instances 0 --conditions talk-trap \
    --out /tmp/seams-playtest-tasks
uvx --quiet --python 3.12 --with pyyaml python flotilla/playtest.py \
    --task /tmp/seams-playtest-tasks/kit_fixture/t-* \
    --seat-cmd 'ASSISTANT --prompt-file {instruction}' --seats p2 \
    --timeout-sec 120 --max-parallel 2 --out /tmp/seams-playtest-out
```

Use unquoted placeholders `{instruction}`, `{seat}`, `{workdir}` and `{log}` in the command template.
The runner quotes their values. Without `--seats`, every subject uses the external command.
Unselected subjects use careful; scripted seats use their built policies.
The output has the verifier result, per-seat logs, and a world event timeline in `summary.md`.
Playtest itself makes no model requests. The external command can incur costs.
Use Playtest for instruction and interaction design, not benchmark evidence.

## Tests

Run `bash flotilla/check.sh` before delivery. It runs both test suites, `ruff check` on the paths listed in
the script, and QA on fresh representative builds. It does not start Docker or use any model.

```bash
bash flotilla/check.sh
uvx --quiet --python 3.12 --with pytest --with pyyaml pytest -q -c flotilla/tests/pytest.ini flotilla/tests
```

They cover every family (determinism across hash seeds, the same seed gives the same instances and
another seed others, oracle properties, robust scoring, no answer in any seat-visible text), the world
over real HTTP (identity, conditions, evidence, limits, no reply or header names the software, a
family or a version), the verifier, the oracle (including whole simulated trials with one process per
seat), the builder (opaque names say nothing, a secret seed per build that no seat sees, `--seed-of`,
the manifest, `make_jobs.py` rosters and its refusal of readable names) and `qa.py` (a fresh opaque
build passes; each planted fault fires its own rule). The `qa.py` tests need PyYAML; without it they
are skipped.
