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

## Usage and elapsed time

Native trials store each seat's usage and timestamps in `result.json.seat_results`.
A null trial-level `agent_result` does not mean zero seat usage.
Cache tokens are part of input tokens; do not add them again.

Each seat's `agent_execution` timestamps give its wall time.
The trial's execution interval gives team wall time, not the sum of seat times.
Neither measures provider latency.

Job usage sums the reported fields from retained attempts, including supporting
seats. Each field is a known subtotal, not a complete bill.
If no retained result reports a field, its value is null.
A reported zero remains zero, including during live retry bookkeeping.
Harbor does not infer missing prices from token counts or price unknown calls.

Retries inside an agent count only when that agent reports their usage.
A queue retry replaces the previous attempt and deletes its directory.
Retained seat records therefore omit discarded attempts and unreported calls.
Job wall time includes discarded work and backoff but cannot assign them to seats.

Before a selected queue retry deletes that directory, Harbor saves its final
scrubbed `result.json` as `retry-attempt-<UUID>.json` in the Job root.
The UUID identifies the discarded attempt even when the Trial name is reused.
An existing destination or a copy failure stops the retry before deletion.

These files do not change retained scoring, usage totals, or retry policy.
They preserve only result bytes, not old logs or trajectories.
The saved `trial_uri` still points to the reused directory.
Batch Hub uploads and Job archives do not include these local sidecars.

## Toward Harbor

We want multi-agent support in Harbor itself. Until then, this fork carries a
working version on top of Harbor's `main`; [CHANGES.md](CHANGES.md) lists
every Harbor file it touches and why. Issues and PRs are welcome.

## License and citation

Apache 2.0, as Harbor. In academic work, please cite Harbor using
[CITATION.cff](CITATION.cff).
