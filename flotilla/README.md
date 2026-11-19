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
