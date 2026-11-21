# harbor-multiagent

[Harbor](https://github.com/harbor-framework/harbor) with several agents in one
environment. Build any multi-agent simulation: games, a company of agents with
coworkers and customers played by other agents, head-to-head competition, or
self-play. Any Harbor harness and model can fill any role, under test or in
support.

By [Olam Labs](https://olamlabs.ai). Not an official Harbor project.

## SeatLab: test what a receiver can do with a peer report

Give your own agent one input from a real 2-seat Harbor task. Change the peer’s report without changing the receiver’s local information. Score the first reply with the stock task. The public Dispatch example needs Python 3.12 or later, but no API key, Docker, or Harbor install.

```sh
git clone --depth 1 --branch experiments/seatlab-live-confirmation https://github.com/PeterMakhnatch/harbor-multiagent.git
cd harbor-multiagent
python3.12 -BS -m examples.seatlab.dispatch_peer_case check
python3.12 -BS -m examples.seatlab.dispatch_peer_case prompt --world 6801 --arm shown
```

The [Dispatch quickstart](examples/seatlab/live-confirmation.md#give-another-receiver-one-real-dispatch-input) shows how to score a reply and divide 8 worlds among workers. Use `masked` instead of `shown` to keep all utility values and report sizes but hide the peer’s bay IDs. Only the model input goes to the worker, not the scorer or answer key.

To replay a complete native booking decision, use a public synthetic calendar case. This command checks the original receiver reply and stock grade, then runs the booking policy. It needs only Python 3.12 or later:

```sh
case_dir=examples/seatlab/results/open-booking/w72
python3.12 -BS -m examples.seatlab.calendar_notices.replay_booking "$case_dir" "$case_dir/first.jsonl"
```

The output identifies the native trial, the route, and both grades. It is **not** a new team reward. [See all 4 open cases, the native results, and how to supply your own receiver](examples/seatlab/calendar-notice-result.md#replay-a-real-booking-from-a-fresh-checkout).

To compare your own harness across tasks, [send 32 real receiver inputs to independent workers](examples/seatlab/replication.md#run-your-receiver-on-2-tasks-without-installing-harbor). Each worker receives one seat’s messages. The offline scorer accepts replies in any order. It reports meeting and shipment scores separately.

Have your own completed scheduling trial? [Send its final receiver input to 2 independent workers](examples/seatlab/replication.md#compare-2-workers-on-your-own-completed-scheduling-trial). Keep the answer key on the original host. The workers need neither Harbor nor an API key to use the file format.

If 2 agents propose different answers, [test a third agent as the selector](examples/seatlab/replication.md#let-a-third-agent-select-between-two-receivers). The test reverses the proposals and scores the selected answer. In [80 real selections](examples/seatlab/live-confirmation.md#can-a-third-agent-choose-between-two-proposed-answers), the reviewer could not improve bookings because neither scheduling proposal was correct.

In the [Dispatch replay](examples/seatlab/live-confirmation.md#do-the-bay-ids-in-a-peers-reports-help-the-dispatcher), actual bay IDs increased optimal choices from 52/96 to 68/96. The [receipt](examples/seatlab/results/dispatch-peer-payload-replay.json) records every receiver call and its token-priced cost.

For an end-to-end test, [run both seats in native Harbor](examples/seatlab/replication.md#check-a-receivers-own-availability-against-delivered-reports). On the [first 24 scheduling worlds](examples/seatlab/calendar-notice-result.md#does-checking-the-received-reports-help-a-live-team), a local calendar check raised bookings from 10 to 20. On [24 different worlds](examples/seatlab/calendar-notice-result.md), bookings rose from 15 to 18. The second test did not establish a team gain.

To diagnose a failed booking, [replay a receiver's question, the sender's answer, and the final choice separately](examples/seatlab/calendar-notice-result.md#does-the-local-sender-check-help-a-whole-team). On 25 recorded states, [letting the receiver ask once more](examples/seatlab/calendar-notice-result.md#should-the-receiver-stop-after-a-verified-rejection) raised correct choices from 9 to 22. This used more communication; these were not new team rewards.

Other starting points: [36 teams exchanging meeting proposals](examples/seatlab/native-model-results.md#does-sending-a-short-proposal-change-the-native-team-reward) and [import a role observation from an Inspect log](https://github.com/PeterMakhnatch/harbor-multiagent/pull/71).

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

To connect an existing harness, try the [calendar team with a prompt-only callback](examples/seatlab/replication.md#try-a-native-team-without-an-api-key). Run the no-key demo first. Then replace the fixed replies with your own model. Harbor runs both seats and scores their actual submissions. Your model stays in a separate host process.

## Toward Harbor

We want multi-agent support in Harbor itself. Until then, this fork carries a
working version on top of Harbor's `main`; [CHANGES.md](CHANGES.md) lists
every Harbor file it touches and why. Issues and PRs are welcome.

## License and citation

Apache 2.0, as Harbor. In academic work, please cite Harbor using
[CITATION.cff](CITATION.cff).
