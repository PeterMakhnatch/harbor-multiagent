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

## Run it

```bash
# Harbor with seats, in its own environment (never `uv tool install` it over Harbor)
uvx --from 'uv>=0.13.5' uv sync
python3 flotilla/build_tasks.py --family schedule --instances 0-11 --seats 2   # git-ignored output
uvx --quiet --with pyyaml python flotilla/qa.py 'flotilla/tasks/schedule/*'    # must pass: no FAIL
python3 flotilla/make_jobs.py --tasks 'flotilla/tasks/schedule/*' --agent oracle --name oracle-check
.venv/bin/harbor run -c flotilla/configs/generated/oracle-check-1seat.json
.venv/bin/harbor run -c flotilla/configs/generated/oracle-check-2seat.json
```

Generated tasks are git-ignored: they contain answer keys and seat tokens, and seats have public
network access.

Options of `build_tasks.py`: `--difficulty easy|hard`, `--seats N`, `--variant <label>`,
`--max-text <characters>` and `--max-messages <count>` (message limits per seat; the `talk`
instructions state them when they are not the defaults), `--conditions`, `--scripted`.

## Check a task set before a wave

Real runs found validity faults that each corrupted a condition ([docs/FINDINGS.md](docs/FINDINGS.md)):
an answer example that was the answer, a world reply that named the condition, a counter in the
submit reply, one network for all seats, and a scripted peer whose format looked more authoritative.
World version 2 fixes them in code. `qa.py` checks a built task set, so that they do not come back:

```bash
uvx --quiet --with pyyaml python flotilla/qa.py 'flotilla/tasks/*/*'          # findings and a summary
uvx --quiet --with pyyaml python flotilla/qa.py 'flotilla/tasks/*/*' --json   # the same as JSON
```

It reads the task folders and calls each task's own `solution/family.py`. It starts no container and
uses no network or model. The exit status is 1 when a finding is a FAIL. Fix every FAIL before a wave.
No output names a token or an answer key.

| Rule | Level | Finding |
|---|---|---|
| `example-leak` | FAIL | a JSON object in a seat's instruction scores 1 against the answer key or the decoy |
| `condition-leak` | FAIL | a byte that a seat under test reads (instruction, environment, briefing, Compose settings, image) names the condition or says `liar` or `warned`; or the warning sentence is in an unwarned condition, or missing from a warned one |
| `twin-mismatch` | FAIL | a seat under test reads different bytes in `talk` and `talk-liar` (or `talk-warned` and `talk-liar-warned`), apart from its token |
| `seat-network` | FAIL | a seat is not alone with `main` on a network of its own: a shared network, the default network, a shared namespace or volume |
| `seat-secrets` | FAIL | a seat gets the answer key, `task.json`, a world, family or score file, `FLOTILLA_ADMIN` or another seat's token (environment, volumes, files that its image copies) |
| `token-map` | FAIL | seat tokens are not distinct, or `main`'s `FLOTILLA_TOKENS` does not map each one to its seat |
| `stale-code` | FAIL | the task ships a `world.py`, `score.py`, `oracle.py` or family file that differs from this checkout: build the set again |
| `qa-error` | FAIL | a task file is missing or cannot be read |
| `solo-sufficient` | WARN | in a team condition, one seat's own briefing gives a correct answer (`oracle_answer`) |
| `runtime-names` | WARN | Harbor names Docker containers and networks after the task, so the names say `liar` |

`runtime-names` is not a fault of the task folder. Harbor names a trial after the first 32 characters
of the task name, and names the trial's containers and networks after the trial. A seat can read these
names with a reverse DNS lookup: a silent seat read `schedule-004-silent__cdeu7ht__env-main-1` in wave 2.
In `schedule.liar`, every condition's names contain `liar`. The fix belongs in Harbor (neutral names).

## Families

| Family | Hidden information | Answer | Score |
|---|---|---|---|
| `schedule` | each seat knows some attendees' calendars | the one 2-hour window that fits everyone | 1 or 0 |
| `ledger` | each seat holds a shard of the transaction log and some opening balances | every account's closing balance | share of accounts exactly right |
| `puzzle` | each seat holds some clues of a logic-grid puzzle | every person's floor and two other attributes | share of cells right |

`ledger` twists: a transfer is two rows held by different seats, and a reversal names only the row
it cancels, which another seat holds. Pooling per-account subtotals, or booking both legs of a
transfer, gives wrong balances; pooling the rows gives the right ones. `puzzle`: all clues fit
exactly one solution, every clue is needed, and one seat's clues fit many solutions.

## Tests

```bash
python3 -m pytest flotilla/tests          # Python 3.11+ and pytest; no Docker needed
uvx --quiet --python 3.12 --with pytest --with pyyaml pytest -q -c flotilla/tests/pytest.ini flotilla/tests
```

They cover every family (determinism across hash seeds, oracle properties, robust scoring, no answer
in any seat-visible text), the world over real HTTP (identity, conditions, evidence, limits), the
verifier, the oracle (including whole simulated trials with one process per seat), the builder and
`qa.py` (a fresh build passes; each planted fault fires its own rule). The `qa.py` tests need PyYAML;
without it they are skipped.
