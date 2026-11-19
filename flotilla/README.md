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
python3 flotilla/make_jobs.py --tasks 'flotilla/tasks/schedule/*' --agent oracle --name oracle-check
.venv/bin/harbor run -c flotilla/configs/generated/oracle-check-1seat.json
.venv/bin/harbor run -c flotilla/configs/generated/oracle-check-2seat.json
```

Generated tasks are git-ignored: they contain answer keys and seat tokens, and seats have public
network access.

Options of `build_tasks.py`: `--difficulty easy|hard`, `--seats N`, `--variant <label>`,
`--max-text <characters>` and `--max-messages <count>` (message limits per seat; the `talk`
instructions state them when they are not the defaults), `--conditions`, `--scripted`.

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
uvx --python 3.12 --with pytest pytest flotilla/tests   # the same with a uv-managed CPython
```

They cover every family (determinism across hash seeds, oracle properties, robust scoring, no answer
in any seat-visible text), the world over real HTTP (identity, conditions, evidence, limits), the
verifier, the oracle (including whole simulated trials with one process per seat) and the builder.
