# Flotilla contract

Flotilla runs controlled multi-agent experiments on `harbor-multiagent` seat tasks. This file fixes
the interfaces that the world service, the task families, the task builder and the analysis share.
Change it only in a pull request that says so in its title.

## 1. The question

A team can beat one agent for two different reasons: it has more workers, or its workers
coordinate. Flotilla separates them by running every task instance in three **conditions**:

| Condition | Seats | Information | Messaging |
|---|---|---|---|
| `solo` | 1 (`p1`) | all private information | none |
| `silent` | N (`p1`..`pN`) | each seat holds only its own part | disabled (HTTP 403) |
| `talk` | N | each seat holds only its own part | enabled |

Optional fourth condition, `talk-liar`: as `talk`, plus one scripted seat (not a language model) that
sends false information at a fixed rate. It measures trust calibration.

For one family, model and instance set, with paired instances:

- `ceiling = score(solo)`, `floor = score(silent)`, `team = score(talk)`
- **coordination gain** `= team − floor`: what messaging adds when information is split
- **recovery** `= (team − floor) / (ceiling − floor)`: the share of the information gap that the
  team closes (1 = as good as one agent that holds everything; 0 = messaging adds nothing)
- **cost ratio** `= tokens(talk) / tokens(solo)`

## 2. Task instances and names

A family module generates instances from `(seed, instance, n_seats, difficulty)`. Every instance is
built in every condition. Task directory and Harbor task name:

```
flotilla/tasks/<label>/<label>-<instance:03d>-<condition>/
task.toml: [task] name = "flotilla/<label>-<instance:03d>-<condition>"
<label> = <family> or <family>.<variant>   (e.g. schedule, schedule.3seat, ledger.hard)
```

A variant changes the seat count or the difficulty of a family. Results of different labels are
never pooled.

`[metadata]` in `task.toml` carries `family`, `instance`, `condition`, `n_seats`, `difficulty`,
`seed`. The analysis parses the task name first and falls back to nothing else.

## 3. Seats and environment

Compose services: `main` (the world; network alias `world`) and one service per seat (`p1`..`pN`).
All seats use one player image (`python:3.13-slim` plus `bash curl git`, user `player`).

| Variable | Where | Meaning |
|---|---|---|
| `SEAT` | each seat | its own seat name |
| `SEAT_TOKEN` | each seat | secret bearer token; only this seat and `main` know it |
| `SEAT_BRIEFING` | each seat | JSON of this seat's private information (the same facts as its instruction) |
| `WORLD_URL` | each seat | `http://world:8080` |
| `FLOTILLA_CONDITION` | main | `solo`, `silent`, `talk` or `talk-liar` |
| `FLOTILLA_SEATS` | main | comma-separated seat names |
| `FLOTILLA_TOKENS` | main | `p1:<hex>,p2:<hex>` |
| `FLOTILLA_ADMIN` | main | admin token used by the verifier only |

`main` also holds `/world/task.json` (family, instance, condition, seats, answer key) and
`/world/family.py` (the family module, for scoring). Seats cannot read `main`'s files.

## 4. The world API (`world/world.py`)

Every endpoint except `/health` needs `Authorization: Bearer <token>`. The world derives the seat from
the token; a client never names its own seat.

| Method and path | Body | Reply | Notes |
|---|---|---|---|
| `GET /health` | | `{"ok": true}` | Compose health check |
| `GET /whoami` | | `{"seat", "condition", "seats"}` | |
| `POST /messages` | `{"to": "<seat>" or "all", "text": str}` | 201 `{"id": "m<k>", ...}` | 403 in `solo` and `silent`; at most 4,000 characters and 200 messages per seat |
| `GET /messages` | | `{"messages": [{"id","from","to","text","t"}]}` | every message to the caller or to `all`, plus its own; 403 in `solo` and `silent` |
| `POST /submit` | `{"answer": {...}}` | 200 `{"accepted": true, "n": k}` | the team answer is the **last accepted submission from any seat**; replies never reveal correctness |
| `POST /admin/freeze` | | 200 | admin token only; later submissions are refused (409) and logged as `late` |
| `GET /admin/state` | | full state | admin token only; used by the verifier |

## 5. Evidence the world writes (artifacts)

`/logs/world/events.jsonl`, one JSON object per line, in server order:

```json
{"seq": 12, "t": 31.402, "seat": "p2", "kind": "send", "data": {"id": "m3", "to": "all", "chars": 118}}
```

`t` is seconds since the world started (monotonic clock). `kind` is one of `whoami`, `send`,
`deliver` (`data.ids`: message ids returned to the caller by `GET /messages`), `submit`
(`data.n`, `data.answer`), `denied` (`data.path`, `data.reason`), `late`, `freeze`, `bad_token`.
Message texts are stored once, in `/logs/world/messages.jsonl` (`id, from, to, text, t`).

`task.toml` lists `events.jsonl` and `messages.jsonl` as artifacts, so they appear under
`<trial>/artifacts/logs/world/`. Harbor collects artifacts **before** the verifier runs, so the
verifier writes its own evidence to `/logs/verifier/` (collected after verification as
`<trial>/verifier/`):

- `score.json`: `{"family", "instance", "condition", "seats", "score", "fields_correct",
  "fields_total", "answer", "submitted_by", "n_submissions", "n_messages", "error"}`;
- `world-state.json`: the frozen world state (every message and submission), including anything
  that arrived after artifact collection.

## 6. Reward

The verifier freezes the world, scores the team answer with the family scorer and writes
`/logs/verifier/reward.json` with exactly one key: `{"reward": <score in [0, 1]>}`. No answer scores 0.
Details go to `score.json`, not to the reward, so that Harbor's own pass@k still works.

## 7. Family module interface (`families/<family>.py`)

Standard library only. Deterministic for a given seed.

```python
FAMILY: str                       # e.g. "schedule"
TITLE: str                        # human title used in instructions

def generate(seed: int, instance: int, n_seats: int, difficulty: str) -> dict:
    """Return {"goal": str, "answer_format": str,
               "private": {seat: data}, "private_text": {seat: markdown},
               "solo_private": data, "solo_text": markdown,
               "answer_key": dict}."""

def oracle_answer(private: list[dict]) -> dict:
    """The best answer computable from the given private parts (all parts: the true answer)."""

def score(answer: dict | None, key: dict) -> dict:
    """{"score": float in [0, 1], "fields_correct": int, "fields_total": int}."""
```

Each family must also have tests: generation is deterministic, `oracle_answer(all parts)` scores 1 on
every instance, and `oracle_answer(one part)` scores clearly below 1 on most instances (the
information is really split).

## 8. Oracle runs (validation, no model)

`solution/solve.sh` runs in every seat under Harbor's `oracle` agent. In `solo` and `silent` it submits
`oracle_answer([own part])`. In `talk` it posts its briefing to `all`, waits until every teammate's
briefing arrived, and submits `oracle_answer(all parts)`. Expected rewards: `solo` 1, `talk` 1,
`silent` = the best a seat can do alone (the information floor).

## 9. Analysis inputs

A Harbor job folder per run wave. Per trial: `result.json` (task name, `seat_results` with agent,
model, usage, exceptions; `verifier_result.rewards.reward`), `artifacts/logs/world/*.jsonl|json`,
`seats/<seat>/agent/trajectory.json`.
