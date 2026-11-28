# Does a correction reach the team in time?

A loader stages a crate. The crate can then move. A depot teammate sees its current bay and chooses whether to report it. The loader must commit to one bay. The simulator grades the committed destination, not the quality of a message, an explanation or a transcript.

The [native two-seat task](../../tasks/dispatch-recall/task.toml) assigns **when** the depot's report can reach the loader. In `immediate` it is available before commit. In `deferred` it is available only after commit. In `withheld` it is never available. Silence leaves all windows blank. The loader cannot wait to change its assignment. The team decides both whether to send and how to act; the task does not measure whether to wait or seek help.

## Check the task before buying a run

From the root of this fork, use Python 3.12 or later. These commands need no installed packages, Docker, credentials or model. All commands below assume a private local working directory.

```sh
umask 077
work=$(mktemp -d /tmp/recall.XXXXXX)
python3.12 -BS -m examples.seatlab.dispatch_recall certify --seed 7 > "$work/certificate.json"
python3.12 -BS -m examples.seatlab.dispatch_recall rehearse --seed 7 --transition move --timing immediate
python3.12 -BS -m examples.seatlab.dispatch_recall rehearse --seed 7 --transition move --timing deferred --cut-out "$work/cut.json"
python3.12 -BS -m examples.seatlab.dispatch_recall diagnose "$work/cut.json"
```

The certificate holds the loader's initial choice, the depot's reporting rule, the request sequence and the test tickets fixed. It compares loader replies through commit across two equally weighted worlds and grades every legal destination using the native world. It considers the close reply separately. The rehearsal instead starts separate scripted role processes that use the actual task sockets, then invokes the native verifier. It is not a Harbor trial, a container isolation test or a model result.

| Depot rule | Best possible before commit: immediate | Deferred | Withheld |
|---|---:|---:|---:|
| Always name the true bay, and the receiver knows the rule | 1 | 1/2 | 1/2 |
| Always name the other bay, and the receiver knows the rule | 1 | 1/2 | 1/2 |
| Always remain silent | 1/2 | 1/2 | 1/2 |
| Truthful or inverted with equal probability, unknown to the receiver | 1/2 | 1/2 | 1/2 |

A receiver that copies a deliberately wrong immediate report scores **0**. A receiver that knows the inverted rule can score **1**. The live loader is not told which rule the depot follows. Free-team reward therefore mixes what the depot sends with how the loader responds. A late truthful report distinguishes the worlds at close. Using it to score the earlier decision would give a false advantage. None of these values predicts model performance.

To qualify another task, use the [four-history decision checker](../decision_boundary/README.md#was-a-correction-useful-before-the-receiver-committed). Its inputs come from what the receiver could actually see and what each legal action could physically achieve.

## Prepare a future study

Register this primary question before looking at any response: **does immediate rather than deferred delivery improve native team reward across independent seed blocks?** Cross both worlds (`stay`, `move`) with all three timings. Treat a seed, not an individual cell or message, as the independent unit. Keep each seat's context isolated between jobs, and balance the roster and inference settings across arms. Keep the roster, case assignment, order seed and output directories outside all participant contexts. Reserve fresh seeds and an independent order seed; the numbers below are public examples.

```sh
python3.12 -BS -m examples.seatlab.dispatch_recall design --seeds 7 19 --order-seed 83 --out "$work/plan.json"
python3.12 -BS -m examples.seatlab.dispatch_recall assess "$work/plan.json"
```

Each seed assigns 6 jobs. Each job has an opaque trial ID and a separate ID for each participant. Separate workers can prepare separate jobs without changing a shared queue. The empty assessment is useful: it shows the full denominator before any trial exists. A missing job is not a failed team.

To prepare native Harbor inputs, use the Python environment for this checkout of the fork. For a new checkout, use `uv sync --locked --no-dev` with the `uv` version required by `pyproject.toml`. Do not install a global Harbor CLI. These commands also require `jq`. They do not launch a seat.

```sh
drstudy() { .venv/bin/python -B -m examples.seatlab.dispatch_recall "$@"; }
mkdir -m 700 "$work/jobs" "$work/owners" "$work/admissions"
for job in $(jq -r '.jobs[].job_id' "$work/plan.json"); do
  drstudy prepare "$work/plan.json" "$job" --owners "$work/owners" --out "$work/jobs/$job" > "$work/admissions/$job.json" || break
done
pin=$(drstudy seal "$work/plan.json" --root "$work/jobs" --out "$work/registry.json" | jq -er .sha256)
drstudy assess "$work/plan.json" --registry "$work/registry.json" --root "$work/jobs" --expect "$pin"
```

`prepare` writes a native config, lock and private receipt. `seal` needs every planned job, distinct owner sockets and the same task and team method. It refuses a missing job or an existing trial directory. Save the digest **outside the trial workspace before the first run**. A digest saved after the run cannot establish prior enrollment. Do not move the workspace after preparation: native configs contain absolute paths.

For a future authorized run, attach one decision owner per socket in the job's config. The loader owns `stage`, `commit` and `close`; the depot owns `report`. The adapter sends each owner only that role's instruction, current view and prior decisions. Use `python3 -IS examples/seatlab/visibility/local_seat.py serve ABSOLUTE_SOCKET -- OWNER_EXECUTABLE` for each turn. The executable reads one JSON line with `schema` and `prompt`. It returns one JSON line with `text` containing the requested action object. Do not share owner state between roles or jobs. The adapter does not authenticate an executable as a particular model.

For an authorized job, read `command` from `admissions/JOB_ID.json` and run it once from the checkout root. `collect PLAN JOB_ID PREPARED --expect RECEIPT_DIGEST` inspects one private archive. Registry assessment checks **all** assignments, including unstarted and incomplete jobs. It rejects inconsistent archives and scripted controls. For an infrastructure check only, `prepare --roster FILE` also accepts an explicit `oracle` or `nop` for both roles. Do not pool scripted controls with free teams. `assess --cut JOB_ID FILE` is an unverified replay, not a native study.

The primary contrast asks which arm wins on a typical non-tied seed. Sum `immediate − deferred` over both worlds for each seed. A positive result requires two things: a two-sided sign test at 0.05, and a strictly positive mean difference across the planned world pairs. Both must survive the worst assignment of missing grades. The mean is descriptive, not a population estimate. The 2 example seeds above cannot pass the sign test; it needs at least 6 non-tied seeds agreeing in direction.

Compare `deferred` with `withheld` as a secondary check. Failure to detect a difference does not establish equality or isolation. Report `stay`, `move`, ties, missing assignments and failures to commit. Freeze the number of blocks, model versions, prompts, owner policy, retry rule and spending cap before launch.

`team_reward` counts a verified failure to commit as 0, including an archived seat error. The secondary `physical_reward` leaves its destination unknown, bounded from 0 to 1. The result is `primary_supported_abstention_sensitive` if team reward improves but the physical comparison cannot establish the same direction. Inspect the exceptions in each collected job. The `truthful_report_cells` counts show how often a depot actually reported the correct bay. They describe selected jobs, not a causal effect.

The assessment also checks each blinded arm against its own no-information reference. A loader with no information about the change cannot average more than 1 success per balanced pair of worlds. The check uses independent seed blocks and a one-sided significance level of 0.025 per arm. `blind_reference_exceeded` means investigate the result. It does not identify a leak. A null result does not certify isolation.

## Read the evidence without inferring intent

A frozen cut separates the depot's opportunity, its actual report or explicit silence, a window requested before or after commit, the committed bay, and closure. A request records that the server prepared a reply. Returning its ticket establishes a protocol exchange, not that a model read or believed the report. Every accepted commit requires the commit ticket. A matching decision therefore does not prove the correction caused it. In the `stay` world, following a truthful report and ignoring it can produce identical actions.

For a frozen prefix with no commit, the Harbor verifier assigns reward `0` and records `missing_commit`. The destination is unknown because no commitment occurred; it is not a wrong bay. A verifier infrastructure failure produces no reward. A correct commit still scores `1` even if the loader fails to close. The private cut names the seed and the actual bays. Never send it, the diagnostics, or the study plan to a participant.

For a host-adapter trial, collection checks the accepted actions and their recorded request hashes against the reconstructed role prompts. It reports whether the commit and close requests contained a bay or a blank. Missing metadata leaves a request unconfirmed. Contradictory metadata does not erase an independently verified physical grade. A matching hash does not identify the model or show that it used the notice.

## What changed from the earlier benchmarks?

In [Revised Dispatch](../../tasks/revised-dispatch/README.md), the receiver policy determines when it acknowledges a correction. Here, the world assigns delivery relative to the same irreversible physical decision. In [Joint Handoffs](../joint_handoffs/README.md), the question is which teammate contributes necessary information. Here, the question is whether that information still has decision value at the instant it becomes available.

[Bespoke MAC Bench](https://benchmarks.bespokelabs.ai/multi-agent-coordination-bench/) describes teams acting on outdated state even when updates exist. [Async-RBench](https://arxiv.org/abs/2610.32429) controls arrival relative to task progress. [When the Plan Changes](https://arxiv.org/abs/2611.10011) separates receiving a notice from acting on it. This task does not measure open-ended coordination or establish frontier-model difficulty.

The relay opens commit only after the depot acts. A participant polling the relay can therefore observe elapsed time. The host adapter omits clocks and tickets from owner prompts. Give each turn a fresh, stateless owner and pass only the explicit role history. The software cannot certify owner independence or exclude a separate channel.
