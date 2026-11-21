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

## Job timestamps

New Job lifecycle timestamps include the UTC offset. The CLI uses one start
instant for local results and Hub records. Supply offset-aware values to
`Job.run(started_at=...)`, Job aggregation, and Hub upload APIs.

Legacy results without an offset remain readable. Their absolute time is
unknown. The viewer keeps unknown starts last in either start-time sort
direction. It excludes them from absolute date filters and does not invent
an active duration.

Resume requires a known stored start. It rejects a start without an offset
before it changes results or logs. An explicit resume start must match the
stored instant. Do not assign a historical zone from the current host settings.

## Native seat measurements

Trials with recorded `seat_results` show one row per seat, including supporting
seats. The table reads recorded agent and model identities, token counts, USD
costs, setup and execution seconds, and errors. Input counts include cached
input. Missing values stay unknown. Known zero values stay zero.

Costs come from the saved result, not a current model price. When an agent
reports counts, the table shows calls with observed or unknown usage. Unknown
usage calls mark partial usage. Recorded costs do not verify a provider bill.
Phase times are recorded wall intervals. Seats can overlap, so their sum is
not the Job's elapsed time. The table does not recover deleted attempts or
unreported billing.

Folder-mode Job metadata uses nonempty participant rosters before the legacy
agent list. The viewer and shared API use the same declared names or full import
paths without importing task modules. Shared lists, details, agent filters, and
facets keep these identifiers consistent. Declared identifiers can differ from
recorded runtime names. Empty rosters keep the legacy agent list. Single-agent
Trials keep the existing Tokens and Timing cards.

Folder state comes from saved progress, not a process liveness check.
The shared API marks a saved Trial as `cancelled` only if it has a finish
time and a Trial-level `CancelledError`. A cancellation recorded only on a
seat or step does not change the Trial state. The saved Job state is unchanged.

## Toward Harbor

We want multi-agent support in Harbor itself. Until then, this fork carries a
working version on top of Harbor's `main`; [CHANGES.md](CHANGES.md) lists
every Harbor file it touches and why. Issues and PRs are welcome.

## License and citation

Apache 2.0, as Harbor. In academic work, please cite Harbor using
[CITATION.cff](CITATION.cff).
