# harbor-multiagent

[Harbor](https://github.com/harbor-framework/harbor) with several agents in one
environment. Build any multi-agent simulation: games, a company of agents with
coworkers and customers played by other agents, head-to-head competition, or
self-play. Any Harbor harness and model can fill any role, under test or in
support.

By [Olam Labs](https://olamlabs.ai). Not an official Harbor project.

## SeatLab workbench

Before running a communication benchmark, [check whether a teammate’s message can change the best decision](examples/seatlab/decision_boundary/README.md). The author tool runs offline on 2 native tasks or your own finite case table. It separates missing information from a missing action.

This branch combines the [team operator](examples/seatlab/workbench/team-account.md), native packaging of per-seat instructions, and a [two-seat shipping benchmark](examples/seatlab/decision_receipt/README.md). Start by checking the saved evidence:

```sh
git clone --depth 1 --branch seatlab/decision-boundary https://github.com/PeterMakhnatch/harbor-multiagent.git
cd harbor-multiagent
python3.12 -BS -m examples.seatlab.workbench verify exhibit-handoff-twins
python3.12 -BS -m examples.seatlab.workbench verify exhibit-authored-channel
python3.12 -BS -m examples.seatlab.workbench verify team-return-partial team-return-complete ship-decision-evidence ship-worker-plans
python3.12 -BS -m examples.seatlab.workbench verify dispatch-native-choices dispatch-order-ready dispatch-order-coverage
```

The 2 exhibit certificates qualify the benchmark without model calls. The `team-return-partial` and `team-return-complete` reports show what changes when a worker is missing. They keep archived model teams separate from scripted controls. The shipping report asks a different question: did the receiver have a usable parcel reference before deciding? The original runs did not record whether the receiver cited it. The [new benchmark](examples/seatlab/decision_receipt/README.md) records the citation separately from the delivery outcome. Preparing its four control configurations does not run them.

To see why the **same two-seat decisions** can earn different rewards, [inspect two saved shipping controls](examples/seatlab/decision_receipt/README.md#compare-two-saved-decisions). The decision card shows the sender's handoff, the receiver's accepted action, and what the supplier did afterward. No seat runs.

To design a new comparison without paying for teams, run `python3.12 -BS -m examples.seatlab.workbench order prepare --example --draws 2`. The [Dispatch benchmark](examples/seatlab/protocol.md#isolate-report-order-without-changing-what-the-sender-said) changes only the order of two peer reports. Its [worker handoff](examples/seatlab/protocol.md#reserve-work-once-when-multiple-operators-share-a-machine) can prevent duplicate local dispatch. To diagnose a finished team, [inspect native choices and both role receipts](examples/seatlab/native-model-results.md#check-one-private-native-trial). None of these commands calls a model.

`list` shows all studies. `verify` with no names checks the entire catalog. Both commands work offline after cloning and need no Harbor install, Docker, or model key.

A verified release means the bundled analyzer reproduces the published report bytes at the registered exit code. It is not a positive study result or a new native trial. Each study report states its own finding.

To act on a participant's return instead of the final team reward, [watch a native seat](examples/seatlab/results/seat-event-stream/results.md) or [wait for a named return from another process](examples/seatlab/results/seat-event-stream/return-gate/results.md). The host records seat state, not prompts or messages. A harness stop does not count as a return.

For a batch, [record each seat in a native `harbor run` job](examples/seatlab/results/seat-event-stream/native-jobs/results.md). If Harbor retries a slot, [keep each execution separately](examples/seatlab/results/seat-event-stream/native-jobs/retry/results.md). The retry report keeps a missing execution in the count and does not assign it a grade.

To inspect recorded usage by seat and retry, [read a saved team job or quote a locked plan](examples/seatlab/workbench/team-account.md). The operator separates harness estimates, token-priced usage and settled provider charges. Charges without a matching record stay unattributed.

Before a future team job, [prepare the host observers and morning manifest together](examples/seatlab/workbench/team-account.md#prepare-observation-before-a-future-job). Nothing starts. Afterward, [check which finished jobs need attention](examples/seatlab/workbench/team-account.md#check-finished-jobs-from-several-workers). A missing job, seat journal, or END receipt is not a failed team.

To see how much teammates' execution windows overlap, [inspect a saved native job](examples/seatlab/workbench/team-account.md#measure-overlap-and-the-last-seat). This works on older jobs without seat journals. It measures elapsed time, not model work.

To tell whether a teammate received a report before a decision, [retain the message journal for each execution](examples/seat_handoffs/README.md#keep-message-evidence-when-harbor-retries-a-slot). You can [check message coverage against a saved host witness](examples/seatlab/results/seat-event-stream/native-jobs/retry/results.md#did-the-message-evidence-survive-too) without running a team.

Before spending on a communication benchmark, [check what the world actually measures](examples/seat_handoffs/author.md). The same offline check distinguishes a returned report from a receiver acknowledgement. A reusable writer lets two authors instrument a new task without changing Harbor.

Before assigning teams, [compare two unrun local trial configs](examples/seatlab/workbench/input-isolation.md#check-trial-configs-before-any-worker-starts) or two saved native locks. Check that only the named role instruction, participant configuration or world setting changes. The command starts no seat.

Harbor applies one roster and one world configuration to a whole job. For a roster or channel comparison, [check two worker jobs](examples/seatlab/workbench/input-isolation.md#check-a-participant-or-channel-comparison-across-two-worker-jobs) against the planned inputs and repeat count. On separate machines, [exchange two small plan receipts](examples/seatlab/workbench/input-isolation.md#hand-a-planned-comparison-to-independent-workers) instead of private configs or paths. `ship-worker-plans` verifies a real handoff without starting either job.

After the jobs finish, [compare returned results without copying either job directory](examples/seatlab/workbench/input-isolation.md#return-results-without-sharing-job-directories). Two saved three-seat jobs reproduce the native verdict from two small JSON files. Run `python3.12 -BS -m examples.seatlab.workbench verify worker-result-return`.

To collect **new native calendar trials** from the shallow clone above, fetch its stock generator once:

```sh
git fetch --depth 1 origin 71ddf40b14ea81dd8eb0c32663e11abfe21cd1e3
```

This does not switch branches or fetch model data. Live trials also need the fork's Python environment, local Docker and a slot wrapper. Use the [new-cohort commands](examples/seatlab/shared-read.md#run-the-same-comparison-on-eight-new-worlds) to divide calendars among workers. Use the [business worker commands](examples/seatlab/market-operations.md#how-independent-workers-run-this-study) to divide companies. The offline replay does not need these steps.

For native runs, use **uv 0.13.5 or later** and a local Docker daemon. Install the fork into its own project environment. Do not replace your global `harbor` command:

```sh
uv sync --locked --no-dev --python 3.12
export HARBOR_PYTHON="$(pwd -P)/.venv/bin/python"
```

Keep your existing site admission wrapper on a shared Docker host. On a workstation **without** a site wrapper, use the [`local-slot` executable](examples/seatlab/workbench/local-slot):

```sh
export DOCKER_SLOT="$(pwd -P)/examples/seatlab/workbench/local-slot"
export SLOT_WRAPPER="$DOCKER_SLOT"
```

It admits at most 2 foreground commands that use the wrapper, across checkouts on the same machine and user account. It does not limit other users or programs that bypass it. It is not a distributed scheduler.

To use your own seat programs, follow the [four-program calendar example](examples/seatlab/external-seats.md#run-one-team). Each process reads a seat prompt and returns one answer. Harbor grades the team. The connector records what crossed each socket; it does not guess the model, the token count or the bill.

To let **two operators collect separate four-seat teams**, use the [campaign commands](examples/seatlab/reporter-campaign.md#start-two-independent-operators). The workers share one private directory. A valid grade of 0 releases the next arm. An interruption preserves the first attempt and leaves unrelated calendars available. The [eight-calendar reporter study](examples/seatlab/certified-reporters.md) shows why you should measure client tokens as well as the number of sessions.

To **repeat a whole business panel**, give each worker [one of two company shards](examples/seatlab/market-handoff.md#run-or-verify-a-separate-repetition). Both workers use the recorded decisions without starting models. To inspect an existing result first, run `python3.12 -BS -m examples.seatlab.workbench verify native-business-repeat reporter-tokens`. The second check shows why the calendar teams used fewer calls but reported more tokens.

The fork's default branch does not include this workbench. Use `seatlab/registrar-authored-handoff` for the combined checkout above. You do not need to switch branches for the linked studies.

**Test a handoff written by another seat:** [The registrar-authored variant](examples/tasks/exhibit-relay/AUTHORED.md) separates what a teammate reported from what the relay delivered. Its development certificate compares the complete requests without starting any model.

**Build a multi-agent handoff benchmark:** [Exhibit Relay](examples/tasks/exhibit-relay/README.md) gives three seats separate records for the same shipment. Its paired worlds have identical per-crate priorities but different best pairs. Run `python3.12 -BS -m examples.seatlab.workbench exhibit twins --seed 7` to check the information loss without any model or container.

## SeatLab: test what a receiver can do with a peer report

Give your own agent one input from a real 2-seat Harbor task. Change the peer’s report without changing the receiver’s local information. Score the first reply with the stock task. The public Dispatch example needs Python 3.12 or later, but no API key, Docker, or Harbor install.

Run these commands from the same checkout:

```sh
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

Before substituting a replay for a team, [compare native grades with repeated calls on the same receiver prompt](examples/seatlab/receiver-replay-fidelity.md#result-screen-changes-offline-and-confirm-them-in-harbor). The 32 matched worlds support screening, not replacing native confirmation.

Other starting points: [36 teams exchanging meeting proposals](examples/seatlab/native-model-results.md#does-sending-a-short-proposal-change-the-native-team-reward) and [import a role observation from an Inspect log](https://github.com/PeterMakhnatch/harbor-multiagent/pull/71).

## SeatLab: decide which multi-agent team to continue

Give an independent supervisor [the first hour of 3 real Bespoke teams](examples/seatlab/continuation-study.md#give-another-supervisor-a-real-decision). Keep the final grades on the owner's machine. Multiple workers can take disjoint sets. Check their choices against the released four-hour outcomes without an API key. In [54 blind calls](examples/seatlab/continuation-study.md#result-do-not-stop-a-team-on-this-evidence), the message text did not justify stopping a team.

To run your own two-seat business task, [stage a real market in local Harbor](examples/seatlab/market-handoff.md#run-one-business-team-in-harbor). Sales sees demand and competition. Finance sees the company's cash and the sales recommendation. Replay needs no key. [Score 12 matched companies](examples/seatlab/market-handoff.md#result-the-correct-sales-recommendation-did-not-pass) without Docker, a simulator install, or a paid call.

For a local campaign, [give independent workers the same sealed market panel](examples/seatlab/market-operations.md#how-independent-workers-run-this-study). They claim different companies and resume after an interruption. To test your own output selector, [split 18 completed-team pairs among blind workers](examples/seatlab/continuation-study.md#give-completed-team-pairs-to-independent-workers). The example runs without an API key.

To test **your own sales and finance agents**, [send blind packets or connect two live programs](examples/seatlab/market-handoff.md#can-an-operator-bring-new-sales-and-finance-seats). Compare the native result with the actual prompts and first replies. The reference programs run without an API key.

Already running 2 Bespoke teams? [Read their owner-side 3-hour marks](examples/seatlab/continuation-study.md#read-marks-from-your-own-bespoke-jobs) without reading their private work. Use the [reported model costs](examples/seatlab/continuation-study.md#does-equal-seat-time-also-match-the-publishers-reported-model-cost) before treating equal seat-time as equal spending.

Need to know whether an agent actually received a message? [Check the owner's private board log locally](examples/seatlab/continuation-study.md#check-a-finished-jobs-owner-side-inbox-without-opening-team-transcripts). It exports counts, not text. If a job resumed after an interruption, [check the company clock separately from the agent timeout](examples/seatlab/continuation-study.md#what-changes-when-the-owner-resumes-a-team-after-an-interruption).

**Do not use an interim business mark as a final reward.** The [stock verifier uses different measures](examples/seatlab/continuation-study.md#is-screening-worth-doing-at-equal-cumulative-team-time). The public archive cannot say what the final reward would have been if a team stopped early.

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
