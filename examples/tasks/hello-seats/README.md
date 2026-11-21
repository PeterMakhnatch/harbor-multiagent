# hello-seats

The smallest seat task: two agents in one environment, posting notes to one
world. See [docs-mintlify/tasks/multi-agent.mdx](../../../docs-mintlify/tasks/multi-agent.mdx)
for the rules.

- `main` runs the world, a Python HTTP server on port 8080 that the agents
  reach as `world`. `GET /notes` lists the notes. `POST /note` takes JSON
  `{"text": ..., "reply_to": ...}` and the seat's token as
  `Authorization: Bearer <token>`. The world gives each note a random `id`
  and sets its `author` to the seat that owns the token. It refuses a post
  without a valid token (401), with an `author` other than the token's seat
  (403), with a body that is not JSON of at most 64 KiB (400), or with a
  `reply_to` that is not a note's `id` (400). The verifier runs here too.
- Each start of the world is a new session. The world writes a new random
  token for each seat to `/tokens/<seat>/token` and starts with no notes. A
  token from an earlier start stops working. The world keeps its notes in
  memory only, so no note outlives the tokens it was posted with.
- Each `/tokens/<seat>` is a named volume that the Compose file mounts in
  `main` and, read-only at `/run/seat`, in that seat only. p1 reads its own
  token at `/run/seat/token`, and no seat mounts another seat's token.
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
- After play ends, the `[[verifier.collect]]` hook in `task.toml` runs
  `world.py save`, which writes the notes, as `GET /notes` returns them, to
  `/logs/world/notes.json`. Harbor saves that file as the trial's artifact.
- `tests/test.sh` runs `tests/grade.py`, which grades that same file. It
  writes reward 1 when p1 has exactly one note, its text is exactly
  `hello from p1`, and its `reply_to` is the id of a note by p2, and 0
  otherwise.

## What a reward of 1 shows

- Harbor ran both agents, each in its own container with its own
  instruction, and both reached the shared world.
- p1's note came with p1's token. The world sets `author` from the bearer
  token, so the note shows that its sender held p1's token, not which
  container sent it. In this Compose file, only `main` and p1's container
  mount p1's token, so the note came from p1's container unless the token
  left it. `tests/runtime/test_hello_seats_docker.py` checks each
  container's mounts. It also runs p2 sending p1's exact note, in reply to
  p2's own note, while p1 does nothing. The world refuses that note or
  credits it to p2, and the reward is 0.
- p1 answered p2. The sender of p1's note knew the random id of p2's note,
  so p1's side acted after p2 had acted. This is the coordination that the
  example teaches: p1 can earn its reward only by responding to p2, and the
  verifier checks the response in the world's record. The reply does not
  show that p1 fetched the note itself: anyone who knows the id can send
  it.
- The record that Harbor saved is the record that was graded.

## What it does not show

- Which process in p1's container sent the note. Every process there can
  read the token, including a background process that p1's agent starts.
- That p1 acted alone. The token is a bearer token: anyone who has it can
  post as p1. A seat can give its token away, for example in a note.
- That p2 did its part well. The reward scores p1 only; p2 only has to post
  a note that p1 can reply to.
- That p1 failed, when the reward is 0. p1 needs a note from p2, so a p2 that
  never posts, or that overloads the world, also gives 0.
- Notes after the save. Harbor does not stop the agents' processes before
  it collects the artifacts and runs the verifier. The world still accepts
  notes then, but a note that arrives after the save is neither graded nor
  saved. A note that a leftover process posts before the save counts.
- Anything from the seats' logs. The verifier does not read `/logs/seats/`:
  each seat writes its own logs, so they are not evidence of what it did.

The credit is right only while 3 conditions hold:

- Harbor, the harnesses and `main` are trusted. `main` holds every token.
- Each token volume is mounted in one seat only. A volume shared by two
  seats, a Docker socket or a privileged container can expose a token.
- No seat can read another seat's traffic. The world speaks plain HTTP on
  the shared network, so a process that captures or redirects that traffic
  can copy a token. The agents run as `player`, without root. Root in a
  seat, with Docker's default capabilities, could redirect a peer's
  traffic.

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
- `artifacts/logs/world/notes.json`: the notes as the collect hook saved them
  after play ended, each with its `id`, `author`, `text` and `reply_to`. The
  verifier graded this file.
- `verifier/test-stdout.txt`: the number of saved notes and p1's notes, as
  the verifier read them.
- `verifier/reward.txt`: `1` when the run worked.
- `result.json`: `agent_info` is p1's agent. `seat_results` lists `p1`
  (`under_test: true`) and `p2` (`under_test: false`), each with its own
  `agent_result` (tokens and cost), timings and `exception_info`.

## Issued tokens and saved output

Harbor's scrubber knows host-side secrets only, so the world lists the tokens that it issues in
`/logs/artifacts/.harbor-secrets`, one per line. After the trial, Harbor reads every `.harbor-secrets`
file under the trial's `artifacts/` and `verifier/` folders, removes those values from every saved
file, and deletes the file. A seat's own folder is not read, so a seat cannot make Harbor erase other
text. Values shorter than 8 characters are ignored.
