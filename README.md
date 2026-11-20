# harbor-multiagent

[Harbor](https://github.com/harbor-framework/harbor) with several agents in one
environment. Build any multi-agent simulation: games, a company of agents with
coworkers and customers played by other agents, head-to-head competition, or
self-play. Any Harbor harness and model can fill any role, under test or in
support.

By [Olam Labs](https://olamlabs.ai). Not an official Harbor project.

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

A [4-analyst experiment](examples/seatlab/team-protocol.md#what-the-4-analysts-did) separates sharing evidence from using it. All 11 teams given a novelty cue shared every private fact. None chose the correct vendor by majority. The [per-seat results](examples/seatlab/results/committee-sharing.json) include the submitted votes, not just the team score.

## Toward Harbor

We want multi-agent support in Harbor itself. Until then, this fork carries a
working version on top of Harbor's `main`; [CHANGES.md](CHANGES.md) lists
every Harbor file it touches and why. Issues and PRs are welcome.

## License and citation

Apache 2.0, as Harbor. In academic work, please cite Harbor using
[CITATION.cff](CITATION.cff).
