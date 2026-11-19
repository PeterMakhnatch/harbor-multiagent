# hello-seats

The smallest seat task: two agents in one environment, posting notes to one
world. See [docs-mintlify/tasks/multi-agent.mdx](../../../docs-mintlify/tasks/multi-agent.mdx)
for the rules.

- `main` runs the world, a Python HTTP server on port 8080 that the agents
  reach as `world`. `GET /notes` lists the notes. `POST /note` takes JSON
  `{"text": ..., "reply_to": ...}` and the seat's token as
  `Authorization: Bearer <token>`. The world gives each note a random `id`,
  sets its `author` to the seat that owns the token, and also writes it to
  `/logs/world/notes.jsonl`. It refuses a post without a valid token (401),
  with an `author` other than the token's seat (403), or with a `reply_to`
  that is not a note's `id` (400). The verifier runs here too.
- At start, the world writes a new random token for each seat to
  `/tokens/<seat>/token`. Each `/tokens/<seat>` is a named volume that the
  Compose file mounts in `main` and, read-only at `/run/seat`, in that seat
  only. p1 can read its own token at `/run/seat/token`, but not p2's.
- `p1` and `p2` are the agents' services: one small image with bash, curl,
  git and Python, running as the user `player`, with `SEAT` set to the
  service's name.
- `p1` is the agent under test. It polls `/notes` until p2's note appears,
  posts `hello from p1` with `reply_to` set to the id of p2's note, and stops.
- `p2` is a supporting agent (`under_test = false`). It posts `hello from p2`,
  then polls `/notes` until p1's note appears. Play ends when p1's agent ends,
  so p2 may still be running then.
- `solution/solve.sh` does what both instructions ask, so the oracle can fill
  both seats.
- `tests/test.sh` reads `/logs/world/notes.jsonl` and writes reward 1 when p1
  has exactly one note, its text is exactly `hello from p1`, and its
  `reply_to` is the id of a note by p2, and 0 otherwise.

## What a reward of 1 shows

- Harbor ran both agents at once, each in its own container with its own
  instruction, and both reached the shared world.
- p1's note came from p1's container. The world sets `author` from the
  token, and only `main` and p1's container can read p1's token. A run where
  p2 posts a note as p1 and p1 does nothing scores 0
  (`tests/runtime/test_hello_seats_docker.py`).
- p1 read p2's note. Note ids are random, and p1's note must give the id of
  p2's note.

## What it does not show

- Which process in p1's container sent the note. Every process there can
  read the token, including a background process that p1's agent starts.
- That p1 acted alone. A seat can give its token away, for example in a
  note, and then any holder can post as that seat.
- That p2 did its part well. The reward scores p1 only; p2 only has to post
  a note that p1 can reply to.
- What happened after play. Harbor does not stop the agents' processes
  before the verifier runs, so a process that is still running can post
  notes while the verifier reads them.
- Anything from the seats' logs. The verifier does not read `/logs/seats/`:
  each seat writes its own logs, so they are not evidence of what it did.

The token stays secret only while no seat can read another seat's files,
environment or traffic. The world speaks plain HTTP on the shared network,
and the agents run as `player`, without sudo. A task that adds a volume
shared by two seats, a Docker socket, a privileged container or root for the
agents removes that guarantee.

## Run it

On a machine with Docker, from the repository root, with `OPENAI_API_KEY`
set:

```bash
harbor trial start -c examples/configs/features/seats-trial.yaml
```

In a checkout of this repository, run it with `uv run harbor ...`. The config
puts Codex with `openai/gpt-5.5` in both seats.
`examples/configs/features/seats-job.yaml` does the same as a job:
`harbor run -c examples/configs/features/seats-job.yaml`.

To run without an API key, set `name: oracle` on both participants and remove
their `model_name` and `model_api_key_name`.

## What to look for

The trial folder is under `./trials/` and is named `hello-seats__<id>`.

- `seats/p1/agent/` and `seats/p2/agent/`: each agent's logs, for Codex its
  `trajectory.json`.
- `artifacts/logs/world/notes.jsonl`: the notes the world accepted, each with
  its `id`, `author`, `text` and `reply_to`.
- `verifier/test-stdout.txt`: p1's notes and the ids of p2's notes, as the
  verifier read them.
- `verifier/reward.txt`: `1` when the run worked.
- `result.json`: `agent_info` is p1's agent. `seat_results` lists `p1`
  (`under_test: true`) and `p2` (`under_test: false`), each with its own
  `agent_result` (tokens and cost), timings and `exception_info`.
