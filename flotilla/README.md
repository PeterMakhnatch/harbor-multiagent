# Flotilla: controlled multi-agent experiments on harbor-multiagent

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

```bash
# Harbor with seats, in its own environment (never `uv tool install` it over Harbor)
uvx --from 'uv>=0.13.5' uv sync
python3 flotilla/build_tasks.py --family schedule --instances 0-11 --seats 2   # a secret seed; git-ignored
uvx --quiet --with pyyaml python flotilla/qa.py 'flotilla/tasks/schedule/*'    # must pass: no FAIL
python3 flotilla/make_jobs.py --tasks 'flotilla/tasks/schedule/*' --agent oracle --name oracle-check
.venv/bin/harbor run -c flotilla/configs/generated/oracle-check-1seat.json
.venv/bin/harbor run -c flotilla/configs/generated/oracle-check-2seat.json
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
compute the answer key; only the manifest, `task.toml` and `task.json` record it. `--seed N` repeats
a build: the canonical waves used `--seed 1`, which is public, so `qa.py` fails such a set
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
uvx --quiet --with pyyaml python flotilla/qa.py 'flotilla/tasks/*/*'          # findings and a summary
uvx --quiet --with pyyaml python flotilla/qa.py 'flotilla/tasks/*/*' --json   # the same as JSON
```

It reads the task folders and calls each task's own `solution/family.py`. It starts no container and
uses no network or model. The exit status is 1 when a finding is a FAIL. Fix every FAIL before a wave.
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
| `twin-mismatch` | FAIL | a seat under test reads different bytes in `talk` and `talk-liar` (or `talk-warned` and `talk-liar-warned`), apart from its token |
| `seat-network` | FAIL | a seat is not alone with `main` on a network of its own: a shared network, the default network, a shared namespace or volume |
| `seat-secrets` | FAIL | a seat gets the answer key, the build's secret seed, `task.json`, `task.toml`, the build's `manifest.json`, a world, family or score file, `FLOTILLA_ADMIN` or another seat's token (environment, volumes, files that its image copies) |
| `token-map` | FAIL | seat tokens are not distinct, or `main`'s `FLOTILLA_TOKENS` does not map each one to its seat |
| `stale-code` | FAIL | the task ships a `world.py`, `score.py`, `oracle.py` or family file that differs from this checkout: build the set again |
| `qa-error` | FAIL | a task file is missing or cannot be read |
| `runtime-names` | FAIL | the set has readable names (`--readable-names`, or a build before opaque names): the Docker names that Harbor derives from a task folder say its condition or label; a WARN with `--allow-readable-names` |
| `public-seed` | FAIL | the set was built with a public seed: below 1,000,000 (such as the canonical `--seed 1`) or named in this checkout's documentation; a WARN with `--allow-public-seed`, only to repeat a published build |
| `container-name` | FAIL | a service sets `container_name`: a seat reads that fixed name by a reverse DNS lookup (Harbor's own names are opaque), and concurrent trials would share it |
| `confederate-plan` | FAIL | a probe task's subject is not the only seat under test, a confederate's briefing is not the family's plan for the task's arm, or `FLOTILLA_SUBMIT_GATE` differs from `task.json` |
| `audit-data` | FAIL | a probe `audit` task's subject can read month data (a CSV line, C, W or a subtotal), the world's audit files do not give the answer key and the workers' numbers, the false post is not the slip that gives the decoy, or the tell is outside the false worker's post in `talk-false-tell` |
| `push` | FAIL | `task.json` `push` is set but not `true`, is on a task that is not a probe task with messaging, or the subject's push note does not match it |
| `inbox` | FAIL | `task.json` `inbox` is set but not `true`, is on a task that is not a probe task with messaging, the subject's "Team messages so far" block is not `task.json`'s scheduled posts exactly, or a seat has a block without `inbox` |
| `solo-sufficient` | WARN | in a team condition, one seat's own briefing gives a correct answer (`oracle_answer`) |

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
| `probe` | none: every seat has the same data files (`~/expenses*.csv`) and question; `p1` is under test, `p2`..`p4` are scripted confederates | one whole-number total | 1 or 0, for `p1`'s own last answer |

`ledger` twists: a transfer is two rows held by different seats, and a reversal names only the row
it cancels, which another seat holds. Pooling per-account subtotals, or booking both legs of a
transfer, gives wrong balances; pooling the rows gives the right ones. `puzzle`: all clues fit
exactly one solution, every clue is needed, and one seat's clues fit many solutions.

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
it added up every row of the category).

`flotilla/trust_card.py` runs one model through the standing trust-card protocol (PROBE.md section 13, `verify`
reliance and `audit` sets): `plan` writes a run_wave plan, `report` analyses the finished wave (docs/NEXT-WAVE.md).

## Tests

The fork runs no CI, so run `flotilla/check.sh` before you push: it runs both test suites, `ruff check`,
and a QA pass on a fresh build of every family and condition (about 1 minute; no Docker, no models).

```bash
flotilla/check.sh
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
