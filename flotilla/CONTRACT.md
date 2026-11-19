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
sends false information at a fixed rate. It measures trust calibration. Every condition whose name
starts with `talk` has messaging: `talk`, `talk-liar`, `talk-warned`, `talk-liar-warned`. The two
`-warned` conditions tell the seats "One of your teammates may give you wrong information."; `talk-liar`
does not. No seat ever sees the name of its condition (section 4).

For one family, model and instance set, with paired instances:

- `ceiling = score(solo)`, `floor = score(silent)`, `team = score(talk)`
- **coordination gain** `= team − floor`: what messaging adds when information is split
- **recovery** `= (team − floor) / (ceiling − floor)`: the share of the information gap that the
  team closes (1 = as good as one agent that holds everything; 0 = messaging adds nothing)
- **cost ratio** `= tokens(talk) / tokens(solo)`

## 2. Task instances and names

A family module generates instances from `(seed, instance, n_seats, difficulty)`. Every instance is
built in every condition. A task's name and its folder name say nothing about the task (no family,
variant, instance or condition):

```
flotilla/tasks/<label>/t-<12 hex>/              the task folder
task.toml: [task] name = "flotilla/t-<12 hex>"
flotilla/tasks/manifest.json                    task name -> what the task is
<label> = <family> or <family>.<variant>        (e.g. schedule, schedule.3seat, ledger.hard)
<12 hex> = the first 12 hex digits of SHA-256(salt, label, instance, condition)
```

`flotilla/tasks` is the build root (`build_tasks.py --out`). The salt is random for each build and is
not stored, so names differ between builds and a name cannot be derived from a task. Why: Harbor names
a trial after the first 32 characters of the task folder's name, and the trial's Compose project,
containers and networks after the trial; Docker's DNS gives a seat these names (a silent seat read
`schedule-004-silent__cdeu7ht__env-main-1`, FINDINGS.md 2026-11-19 06:20). The folder `<label>/` only
groups a label's tasks for the operator: Harbor names nothing after the path above the task folder,
and no seat sees that path.

`manifest.json` lies at the build root, outside every task folder, so no seat reads it. It maps each
task name to `path` (relative to the build root), `label`, `family`, `variant`, `instance`,
`condition`, `seats` and `built` (UTC). It holds no token and no answer key:

```json
{"format": "flotilla-manifest", "version": 1, "tasks": {"flotilla/t-3f9c2a7b10de": {"path": "schedule/t-3f9c2a7b10de",
  "label": "schedule", "family": "schedule", "variant": "", "instance": 4, "condition": "silent",
  "seats": ["p1", "p2"], "built": "2026-11-19T18:00:00Z"}}}
```

A build replaces each task folder of the same label, instance and condition that an earlier build
wrote (readable or opaque), so a label never holds two folders of one task. The manifest keeps every
name built at its root, also the names of replaced folders, so that jobs of an earlier build can still
be read. Do not publish a manifest while its tasks can still run: a seat that reads its own name could
look the name up.

`build_tasks.py --readable-names` writes the layout used before opaque names, for debugging only:
`flotilla/tasks/<label>/<label>-<instance:03d>-<condition>/`, task name
`flotilla/<label>-<instance:03d>-<condition>`. `qa.py` warns about such a set (`runtime-names`); never
run one in a wave. `qa.py` fails a task built with opaque names whose task name or folder name is not
of the opaque form or names a condition, its family or its variant (`opaque-names`).

A variant changes the seat count or the difficulty of a family. Results of different labels are
never pooled.

Before a wave, every built task set must pass `flotilla/qa.py` with no FAIL finding (README.md,
"Check a task set before a wave"). A set that fails is fixed and built again, never run.

`[metadata]` in `task.toml` carries `family`, `variant`, `instance`, `condition`, `n_seats`,
`team_seats`, `difficulty`, `seed`, `max_text`, `max_messages`, `play_timeout_sec` and `names`
(`opaque` or `readable`), and `scripted_seats` in scripted variants. `environment/task.json` also
carries `family`, `variant`, `instance` and `condition` (section 3). No seat reads either file, nor
the manifest (`qa.py seat-secrets`).

The analysis learns what a trial's task is from a readable task name, else from `task.toml`
`[metadata]` in the trial's task folder (`task_id.path` in `result.json`) when that folder still holds
the task of that name, else from a build manifest: one given with `flotilla-analyze --manifest`, the
one in a `--tasks` folder, or the one at the build root of the task folder.

## 3. Seats and environment

Compose services: `main` (the world) and one service per seat (`p1`..`pN`). All seats use one player
image (`python:3.13-slim` plus `bash curl git jq`, user `player`). Each seat has its own network,
`seat-<seat>`, shared only with `main`, which has the alias `world` on every one of them: a seat reaches
the world, never another seat. (Before world version 2 all seats shared one network, and a seat could
open a socket to another seat, a channel that bypasses the world even in `silent`.) Every value in the
Compose file is written with `$` as `$$`, so that Compose never replaces `$NAME` in a briefing with a
variable of the host that runs the trial.

| Variable | Where | Meaning |
|---|---|---|
| `SEAT` | each seat | its own seat name |
| `SEAT_TOKEN` | each seat | secret bearer token; only this seat and `main` know it |
| `SEAT_BRIEFING` | each seat | JSON of this seat's private information (the same facts as its instruction) |
| `WORLD_URL` | each seat | `http://world:8080` |
| `FLOTILLA_CONDITION` | main | `solo`, `silent`, `talk`, `talk-liar`, `talk-warned` or `talk-liar-warned` |
| `FLOTILLA_SEATS` | main | comma-separated seat names |
| `FLOTILLA_TOKENS` | main | `p1:<hex>,p2:<hex>` |
| `FLOTILLA_ADMIN` | main | admin token used by the verifier only |
| `FLOTILLA_MAX_TEXT` | main | characters per message (default 4,000; `build_tasks.py --max-text`) |
| `FLOTILLA_MAX_MESSAGES` | main | messages per seat (default 200; `build_tasks.py --max-messages`) |

`main` also holds `/world/task.json` (family, variant, instance, condition, seats, answer key,
`world_version`, `max_text`, `max_messages`) and `/world/family.py` (the family module, for scoring).
Seats cannot read `main`'s files. When the limits are not the defaults, the instructions of the `talk`
conditions state them ("Each seat may send at most 3 messages of at most 200 characters.").

## 4. The world API (`world/world.py`)

`WORLD_VERSION = 2`. Every endpoint except `/health` needs `Authorization: Bearer <token>`. The
world derives the seat from the token; a client never names its own seat. **No reply to a seat names
the condition**: a seat that reads `talk-liar` knows that someone lies (world version 1 returned the
condition from `/whoami`, and seats read it).

| Method and path | Body | Reply | Notes |
|---|---|---|---|
| `GET /health` | | `{"ok": true}` | Compose health check |
| `GET /whoami` | | `{"seat", "seats", "messaging": bool, "limits": {"max_text", "max_messages"}}` | |
| `POST /messages` | `{"to": "<seat>" or "all", "text": str}` | 201 `{"id": "m<k>", ...}` | 403 when messaging is off (`solo`, `silent`); 413 above `max_text` characters; 429 after `max_messages` messages of this seat |
| `GET /messages` | | `{"messages": [{"id","from","to","text","t"}]}` | every message to the caller or to `all`, plus its own; 403 when messaging is off |
| `POST /submit` | `{"answer": {...}}` | 200 `{"accepted": true}` | the team answer is the **last accepted submission from any seat**; replies never reveal correctness or how many submissions exist (world version 1 returned the running count `n`, which a seat in `silent` read as a signal from its teammates) |
| `POST /admin/freeze` | | 200 | admin token only; later submissions are refused (409) and logged as `late` |
| `GET /admin/state` | | full state, with `world_version`, `condition` and `limits` | admin token only; used by the verifier |

Any other path is 404 and a known path with the wrong method is 405 (with `Allow`); a seat that asks
for an `/admin/` path gets 404. A body that is not a JSON object of at most `max(64,000,
8 × max_text)` bytes is 400; `NaN` and `Infinity` are not JSON here.

## 5. Evidence the world writes (artifacts)

`/logs/world/events.jsonl`, one JSON object per line, in server order:

```json
{"seq": 12, "t": 31.402, "seat": "p2", "kind": "send", "data": {"id": "m3", "to": "all", "chars": 118}}
```

`t` is seconds since the world started (monotonic clock). Every request leaves one event, except the
Compose health check that `main` sends to itself. `kind` is one of:

- `start` (`data.condition`, `data.seats`, `data.world_version`, `data.limits`), written once;
- `whoami`, `send` (`data.id`, `data.to`, `data.chars`), `deliver` (`data.ids`: message ids returned
  to the caller by `GET /messages`), `submit` (`data.n`, `data.answer`), `late` (`data.answer`),
  `freeze`, `state` (the verifier read the state);
- `denied` (`data.path`, `data.reason`): messaging is off, or the seat's message limit is reached;
- `bad_token` (`data.path`): a missing or unknown token (401);
- `probe` (`data.method`, `data.path`, `data.status`): an unknown path (404, including a seat that asks
  for an `/admin/` path) or a wrong method (405), so exploration is measured;
- `invalid` (`data.method`, `data.path`, `data.status`, `data.reason`): a body the endpoint cannot use
  (400, or 413 for a message above the text limit);
- `health`: `GET /health` from another container than `main`.

Message texts are stored once, in `/logs/world/messages.jsonl` (`id, from, to, text, t`).

`task.toml` lists `events.jsonl` and `messages.jsonl` as artifacts, so they appear under
`<trial>/artifacts/logs/world/`. Harbor collects artifacts **before** the verifier runs, so the
verifier writes its own evidence to `/logs/verifier/` (collected after verification as
`<trial>/verifier/`):

- `score.json`: `{"family", "instance", "condition", "seats", "world_version", "score",
  "fields_correct", "fields_total", "answer", "submitted_by", "n_submissions", "n_messages", "error",
  "deceived"}`. `world_version` is 1 for tasks built before world version 2. `error` is set when the
  world was unreachable or the family scorer raised on the answer (the score is then 0); `deceived`
  is set in scripted-liar variants (did the team submit the decoy?);
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

Optional: `parse_text(text) -> dict | None` reads a private part back from its plain text (the
oracle uses it to read a scripted peer). `score` must never raise, whatever JSON object it receives,
and must never give credit for a wrong value.

Optional analysis hooks (`flotilla-analyze` reads messages with them; they never change a score):
`carries_own_data(text, private) -> bool` says whether a message text carries its sender's own
information (`private` is the sender's private part), and is false for requests, including requests that
quote a format example; `answer_in_text(text, key) -> bool` says whether a message text names the answer
`key`. `schedule` implements both.

Each family must also have tests (`flotilla/tests/`, run with `python3 -m pytest flotilla/tests`):
generation is deterministic, `oracle_answer(all parts)` scores 1 on every instance, and
`oracle_answer(one part)` scores clearly below 1 on most instances (the information is really split).
No text that a seat reads (goal, answer format, private text) contains the answer key; in
particular, a format example is never the answer.

## 8. Oracle runs (validation, no model)

`solution/solve.sh` runs in every seat under Harbor's `oracle` agent. When `/whoami` says messaging
is off it submits `oracle_answer([own part])`. When messaging is on, it posts its briefing to `all` as
numbered chunks that fit the limits (`FLOTILLA-BRIEFING <i>/<n> <compact JSON piece>`), or
`FLOTILLA-BRIEFING 0/0` when the briefing cannot fit (nothing is coming); it waits until every
teammate is done (at most 300 seconds) and submits `oracle_answer(all parts)`. A scripted peer posts
its part, true or false, as plain text only; the oracle reads it with the family's `parse_text`.
Expected rewards: `solo` 1, `talk` 1, `silent` = the best a seat can do alone (the information floor).
With limits too small for the briefings, `talk` falls to the `silent` floor.

## 9. Analysis inputs

A Harbor job folder per run wave. Per trial: `result.json` (task name, `task_id.path`, `seat_results`
with agent, model, usage, exceptions; `verifier_result.rewards.reward`),
`artifacts/logs/world/*.jsonl|json`, `seats/<seat>/agent/trajectory.json`. For opaque task names, the
task folder's `task.toml` or the build's `manifest.json` (section 2): keep the manifest with the jobs
when the task folders are removed.
