# harbor-multiagent

[Harbor](https://github.com/harbor-framework/harbor) with several agents in one
environment. Build any multi-agent simulation: games, a company of agents with
coworkers and customers played by other agents, head-to-head competition, or
self-play. Any Harbor harness and model can fill any role, under test or in
support.

By [Olam Labs](https://olamlabs.ai). Not an official Harbor project.

## Replay an agent handoff without installing Harbor

SeatLab is a fork-only experiment. It lets you change what a replacement agent sees without changing the world or rerunning the departing agent. Start with two service desks and a revised opening time:

```sh
python -m examples.seatlab.handoff plan \
  --packet examples/seatlab/handoff/rota.json \
  --sha256 "$(shasum -a 256 examples/seatlab/handoff/rota.json | cut -d' ' -f1)"
```

Run this command from this branch with Python 3.12 or later. It needs no account or Docker. The [working example](examples/seatlab/replication.md#run-a-handoff-on-your-own-records) shows how to run two programs, capture their first answers, and score them. It also accepts your own records.

To start from real Harbor data, use the [saved trial packet](examples/seatlab/replication.md#repeat-the-handoff-comparison-on-saved-native-harbor-trials). No private archive is needed. From your own completed handoff, you can compare [shorter notes](examples/seatlab/replication.md#can-a-model-pack-an-actual-outgoing-note-better-than-a-byte-prefix) or [one worker per answer](examples/seatlab/replication.md#does-assigning-answer-ownership-prevent-incomplete-handoffs). Each worker claims a separate first attempt. The [results](examples/seatlab/native-model-results.md#does-a-revised-record-help-a-replacement-agent-find-the-right-context) distinguish a better lookup from a better final decision.

## Install

```bash
uv tool install git+https://github.com/olamlabs/harbor-multiagent
```

This installs the `harbor` command in place of an installed Harbor.
Single-agent tasks run unchanged.

## Example

Each agent runs in its own service of the task's
`environment/docker-compose.yaml`, reads `instructions/<service>.md`, and is
named in `task.toml`:

```toml
[agent]
timeout_sec = 600.0

[agents.p1]

[agents.p2]
under_test = false
```

A trial or job config puts a harness and model in each one:

```yaml
participants:
  - seat: p1
    name: codex
    model_name: openai/gpt-5.5
  - seat: p2
    name: codex
    model_name: openai/gpt-5.5
```

Run the example task, [examples/tasks/hello-seats](examples/tasks/hello-seats),
with Docker running and `OPENAI_API_KEY` set:

```bash
harbor trial start -c examples/configs/features/seats-trial.yaml
harbor run -c examples/configs/features/seats-job.yaml
```

[docs-mintlify/tasks/multi-agent.mdx](docs-mintlify/tasks/multi-agent.mdx)
covers the task format, participants, how a run starts and ends, logs, the
verifier, and the limitations.

### Measure what a team did with a message

[SeatLab](examples/seatlab/native-model-results.md) compares the server's message log with the agents' submitted actions. It includes 616 archived trials and a new experiment with 2 model seats. The [replication guide](examples/seatlab/replication.md#run-2-new-model-seats-in-harbor) shows how to run the experiment, record model costs, and read the results with stock Eval Lab. The published results need no API key.

To run your own inventory and dispatch agents in real Harbor trials, start with [Run two real seat programs](examples/seatlab/replication.md#run-two-real-seat-programs). Each dispatcher can open 2 of 6 messages. The [first 24 teams](examples/seatlab/native-model-results.md#can-a-native-dispatcher-use-revision-notices-to-allocate-two-reads) show how to compare a context change against the native reward. You can verify the public report without running another model.

To compare two agent programs, start with [Run your own reviewer and decision maker](examples/seatlab/replication.md#run-your-own-reviewer-and-decision-maker). The included packet and demo need only Python. Give the reviewer and coordinator separate executables to run real agents. The [delegation experiment](examples/seatlab/native-model-results.md#does-identifying-a-reviewers-track-record-improve-delegation) shows when a small piece of shared history changed which teammate the model asked.

To test feedback rather than fixed history, [run your own team through a stock change](examples/seatlab/replication.md#run-different-agent-programs-on-the-same-stock-changes). Give each role a separate executable. The next request uses only what that team learned from its previous decisions. The included stock checker needs no model or Docker.

To test how one agent hands work to its replacement, [run a handoff on your own records](examples/seatlab/replication.md#run-a-handoff-on-your-own-records). Bring two agent programs and a small JSON file. The runner gives each program only its part. It keeps the first attempt and scores the replacement's choices.

## Toward Harbor

We want multi-agent support in Harbor itself. Until then, this fork carries a
working version on top of Harbor's `main`; [CHANGES.md](CHANGES.md) lists
every Harbor file it touches and why. Issues and PRs are welcome.

## License and citation

Apache 2.0, as Harbor. In academic work, please cite Harbor using
[CITATION.cff](CITATION.cff).
