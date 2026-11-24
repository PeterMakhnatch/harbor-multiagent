# Account for a multi-agent Harbor job without running it

Start with the released data. Use Python 3.12 or later from this checkout. These commands do not install Harbor, run containers, contact a provider, or read API keys.

Before approving a Dispatch Handoff study, [register its workload](../protocol.md#review-a-dispatch-panel-without-running-it). The packet fixes each role's maximum requests and total requested output. It is not a price quote.

```sh
wb=examples.seatlab.workbench
python3.12 -BS -m "$wb" account calendar
```

The released calendar study contains **96 graded teams**, not 384 independent trials. The two seats made 384 recorded calls. The saved grades are 57 ones and 39 zeros. Both seats have the served label `deepseek-flash`. The saved usage prices to **$0.014291868** at the study's recorded rates. That is not a provider invoice. The archive contains neither a generation ID nor a full-account bill. It cannot say what another model would cost or whether a different team would succeed.

## Use a saved native job

Stop the writers or make a stationary snapshot first. Point at a native Harbor **job** with a top-level `result.json`. Do not point at a task checkout or an individual trial directory.

```sh
python3.12 -BS -m "$wb" account native "$job"
python3.12 -BS -m "$wb" account native "$job" --trial "$trial_uuid" --json
```

The report counts registered slots, surviving results and valid, invalid or unknown grades. A valid zero is an observation. A missing file is not a grade. `--trial` adds one execution; header totals cover the whole input. A native job is closed only when it records a finish and completes all registered slots. A snapshot of an open job can still change. The reported harness, provider, model and served label are separate. An OpenRouter route does not establish which model served a request.

To check which registered team inputs the retained final grades support, use the [input census](input-isolation.md#check-which-inputs-reached-the-retained-final-results). Slot names alone cannot identify an experimental condition.

| Amount | Source | What it means |
| --- | --- | --- |
| Harness USD | Each native seat's `agent_result.cost_usd` | A reported subtotal with known and unknown seats. Other seats do not disappear when one has no price. |
| Token-priced USD | The released study's joined seat, turn and usage receipts | A calculation at the recorded rates. Input already includes cached input. Do not add cache a second time. |
| Provider USD | Settlements in a local host ledger, or saved OpenRouter generation details | A team subtotal only for exact request-to-seat matches. It is not all spend on the account. |

Do not add those columns. They can describe the same call. Keep JSON for exact source decimals. Digits beyond billing precision can come from the harness's serialized floating-point estimate.

### Measure overlap and the last seat

Use a stationary native job. The default reads the wall-clock windows saved in `seat_results[].agent_execution`. It needs no observer. For a job with host seat journals, choose the monotonic clock explicitly:

```sh
python3.12 -BS -m "$wb" timing "$job" --roles
python3.12 -BS -m "$wb" timing "$job" --clock journal --json
```

`shared_ns` is time with at least two selected seat windows open. `all_ns` needs every selected seat. `occupied_ns` is the union of their windows; gaps count in `span_ns` but not `occupied_ns`. `seat_ns` sums seat durations and can exceed the span. `last_alone_ns` counts only the final seat's own time after all the others ended. A tied end adds no tail. `--roles` identifies the last seat by its local label. By default labels are hidden.

The result clock includes harness setup inside the seat execution and the wait for trajectory collection. The journal clock starts just before `agent.run` and ends when the harness records `play_end`. A stopped seat can have an end on either clock. A timestamp is not proof that the model returned normally. The operator never combines the two clocks, fills gaps with zero, or aligns different trials on one timeline.

The default selects every seat. `--under-test` omits supporting seats. Fewer than two selected seats cannot yield a team timing measurement. The report separates enrolled slots, retained final results and finals with complete windows. It excludes displaced retries. Totals sum the **measured finals**, not the elapsed time of the job or the time of an unobserved seat.

Exit `0` means a closed job has complete windows for all enrolled finals. Exit `3` means coverage or closure is incomplete. Exit `1` flags a clock contradiction; exit `2` rejects invalid input or an identity mismatch. Missing evidence does not establish that a seat failed. The host windows include waits. Do not infer messages, billed time, or achievable speedup from overlap alone.

On a saved job with 20 three-seat model teams, 20 finals had wall-clock windows. The spans summed to 1,475.87 seconds. The last seat alone accounted for 850.06 seconds (57.6%). Seat `p3` finished last in 8 of 20 finals and accounted for 615.71 of those seconds. None of the 20 had host seat journals. This identifies a role to inspect, not time or money that could have been saved.

For a reproducible example, use `timing examples/seatlab/results/seat-event-stream/native-jobs/job-return --clock journal`. The two published scripted trials record 0.54 seconds of overlap and 10.00 seconds with the last seat alone in total. Their excerpt omits the job finish, so the command returns `3` despite measuring both trials. The same excerpt with `--clock result` gives no timing measurement.

### Prepare observation before a future job

Use `instrument` when you control the host that will run a native **multi-seat job**. It prepares the host observers and the manifest for the morning check in one step. It does not run Harbor, create the job directory, reserve capacity, contact a provider, or grant spending permission.

```sh
umask 077
root=$(mktemp -d)
root=$(cd "$root" && pwd -P)
job="$root/native-team-one"
bundle="$root/evidence-one"
python3.12 -BS -m "$wb" instrument "$job" "$bundle" > "$root/prepared.json"
pin=$(python3.12 -BS -c 'import json,sys; print(json.load(open(sys.argv[1]))["spool_sha256"])' "$root/prepared.json")
```

Both paths must be new. This example uses temporary storage. For a future campaign, use durable parent directories on the same filesystem that you control. Keep the bundle outside the job and all seat mounts. Give each job its own bundle. Add `--handoffs` only for a benchmark that emits the [message evidence contract](../../seat_handoffs/author.md).

Keep the spool digest outside the worker's write access. `flags.json` contains one JSON array: each string is a separate argument for a future, separately authorized local `harbor run`. Preserve element boundaries, including spaces. Do not pass the file as one flag or evaluate its contents in a shell. The arguments select the observers, not the task, budget, or output. Set the native output to the enrolled path (`-o` for its parent and `--job-name` for its name). The first observer refuses a different job directory before native trials run. Do not combine bundles or add a second seat observer. Server jobs and `harbor trial start` do not support these local job plugins.

To inspect the prepared bundle, use only the reader:

```sh
python3.12 -BS -m "$wb" attention "$bundle/spool.json" --expected-spool "$pin"
```

Before the job exists, the correct result is `absent`, exit `3`. After a stationary job closes, the report compares START receipts, seat journals and END accounts by execution and slot. Matching totals alone are not enough. A missing END account does not prove an execution failed or cost nothing. A START match does not identify a treatment; use [`cohort --starts`](input-isolation.md#capture-which-inputs-started) for that check.

For workers on separate disks, each worker sends only `prepared.json` before assignment. Keep a coordinator-owned list of all expected workers and their pins. Later, request each worker's `attention --json` report and exit status. Match the spool pin and requested checks, and reject duplicate job UUIDs. Report **not received** when a worker sends nothing; **absent** means a worker checked its enrolled path and found no job. Neither means a team failed. The worker reports are not signed. Do not send `spool.json` or `flags.json`: they contain private paths.

### Hand off independently collected jobs

Each worker exports one stationary job. The receiver needs no access to its machine, model key, prompts or trajectories. These packets contain job and execution identifiers, seat labels and numeric evidence. Inspect labels before sharing them.

```sh
# Worker A; worker B exports its own job in the same way.
python3.12 -BS -m "$wb" account native "$job" --packet > worker-a.json
# Coordinator; a retransmission of the same packet is safe.
python3.12 -BS -m "$wb" account merge worker-a.json worker-b.json worker-a.json --json
```

An identical projection counts once, even if the JSON layout changes. Different snapshots of one job do not merge by timestamp. Select the authoritative copy. Two jobs that claim the same execution fail validation. Missing participants retain null costs. An entire worker that sent no packet is outside the report: the command cannot reconstruct an unseen job registration.

### Check finished jobs from several workers

Give the coordinator one JSON manifest. Each row names one native job directory. Paths resolve from the manifest's directory, not the shell. Add only the evidence you intend to check.

Put the manifest in a private leaf directory. Give each worker a subdirectory below it. Do not use `..` in a row.

```text
morning/                    owner-only; never share it
  spool.json
  worker-a/job/             worker A's native job is copied here
  worker-a/account-receipts/
  worker-b/job/
  worker-b/seat-journals/
  worker-b/handoff-receipts/
  worker-b/witness.json
```

```json
{
  "schema": "seatlab.workbench.spool/v1",
  "jobs": [
    {"job": "worker-a/job", "accounts": "worker-a/account-receipts"},
    {"job": "worker-b/job", "seats": "worker-b/seat-journals", "handoffs": "worker-b/handoff-receipts", "witness": "worker-b/witness.json"}
  ]
}
```

Enroll every job **before** you assign work. List each job path, even when its directory does not exist yet. Then record the manifest's SHA-256:

```sh
umask 077
pin=$(python3.12 -BS -c 'import hashlib,sys;f=open(sys.argv[1],"rb");print("sha256:"+hashlib.file_digest(f,"sha256").hexdigest())' morning/spool.json)
echo "$pin"
```

Keep the pin where no worker can write. Do not change `spool.json` after this step. In the morning, give the pin to the check:

```sh
python3.12 -BS -m examples.seatlab.workbench attention morning/spool.json --expected-spool "$pin"
python3.12 -BS -m examples.seatlab.workbench attention morning/spool.json --expected-spool "$pin" --json
```

The pin compares the exact manifest bytes before the command reads a job. A removed, added, reordered or reformatted row gives exit `2` with no report. The pin is a file digest, not a signature. It does not identify the author or prove that a worker ran. A person who can change the manifest and the stored pin can change both. Without `--expected-spool`, the report covers only the rows in the supplied file. It cannot show that a row was removed. The report gives `spool_sha256` for the bytes it read; compare it with your pin.

Each report row has `row`, the 1-based position in the private manifest, and `job`, the native job UUID. A row is `absent` when its job directory does not exist or has no top-level `result.json`. An absent row has `job: null`. It is not a grade, a failure or a finding. A broken symlink, a file in place of a directory, an invalid result, or a job that appears during the read gives exit `2`, not `absent`. The report shows job UUIDs and row numbers, not paths, prompts or seat labels. Keep the manifest local; its paths can identify workers.

`starts` checks the native START receipts. `accounts` checks END accounts against the surviving final results. `seats` checks host seat journals. `handoffs` checks message receipts. Add `witness` only with `seats` for an independent slot observation. Without these fields, the command checks only the native results. Two paths to the same job do not count twice.

| Exit | Meaning | What to do |
| --- | --- | --- |
| `0` | Every listed row is a closed job. The requested checks found no gaps. | Report the scope checked, not a blanket pass. Without a pin, the scope is only the supplied rows. This is not an authorization to spend. |
| `1` | At least one closed job has a finding, whatever the other rows are. | Inspect its job UUID and finding codes. The same job can have more than one kind of gap. Do not sum them as failed trials. |
| `2` | The manifest does not match the pin, or the manifest or its evidence is invalid, unreadable or changed during the read. | Use a stationary copy. Fix the input before interpreting any result. Do not edit the manifest to match; enroll again before new work. |
| `3` | No closed job has a finding, but a row is open, unknown or absent. | Obtain the final native result if available. Do not treat the job as a failure or start a replacement. |

Try the [included manifest](morning-example.json) with `attention examples/seatlab/workbench/morning-example.json`. Its two published job excerpts omit finish records. The expected exit is `3`.

On a separate closed job with 20 final attempts, replaying the saved END records reproduced all 20 final accounts. This does not establish that an observer captured them during the job. The saved finals had no supported message receipts. Without an independent START archive, the report gave `start_receipt_final_missing: 20` and `start_receipts_under_expected: 20`. These describe the same 20 finals, not 40 failed teams.

### Recover evidence without changing a grade

Use `--fragment` when trial directories from this job were moved to another directory. Repeat the option for another archive. The reader checks each result's job UUID, execution UUID and native directory name. It does not use a task label, timestamp, filename prefix or trajectory to guess membership.

```sh
python3.12 -BS -m "$wb" account native "$job" --fragment "$displaced" --json
```

A matching copy of a final adds nothing. A distinct displaced execution can contribute recorded usage to a **separate subtotal**, even when its slot has no final. A candidate never supplies a final reward, even when it is the only candidate. A directory with no result supplies no execution. Do not add detached subtotals from separate runs of this command; give it all fragment directories together.

For a future authorized job, the optional `examples.seatlab.workbench.account_keeper:AccountKeeper` plugin captures each completed attempt before Harbor can remove its directory for a retry. Configure a new owner-only, absolute `account_dir` **outside** the job tree. Its plugin arguments are `--plugin examples.seatlab.workbench.account_keeper:AccountKeeper --plugin-kwarg account_dir=/absolute/private/accounts`. This is a passive host observer, not a request collector or a spend limit. No END event means no receipt. On receipt conflict it keeps the first and warns. A missing capture stays missing.

```sh
python3.12 -BS -m "$wb" account native "$job" --archive "$account_dir"
```

This compares retained receipts to final results. Confirmed earlier attempts never add a final grade. A complete earlier-cost total requires agreement on the final captures, retry count and seat prices. The two archive modes have different proof rules. Choose one per report.

### Reconcile an existing provider ledger

```sh
python3.12 -BS -m "$wb" account native "$job" --ledger "$events" --owners "$owners" --json
```

`--ledger` accepts the host's saved OpenRouter settlement JSONL, not a dashboard CSV or arbitrary JSON. It verifies `openrouter_generation.total_cost` against the ledger's nanodollar rounding policy. Omit `--owners` to see how much is unassigned. A failed or unsettled call is not proof that it was free.

If you have [OpenRouter generation details](https://openrouter.ai/docs/guides/features/logs) instead of the host ledger, keep one **already saved** response per line. Do not include a dashboard export or a chat response. Use the same owner records:

```sh
python3.12 -BS -m "$wb" account native "$job" \
  --generation-details "$saved_details" --owners "$owners" --json
```

A minimal line is `{"data":{"id":"gen-example","total_cost":0.0015}}`. The reader uses `data.id`, not `request_id` or `upstream_id`. It reads `data.total_cost` as an exact decimal, not a token estimate. It does not call OpenRouter, print raw IDs, or copy other fields from the provider. Keep the input private: a full response can include user and routing metadata.

Import the ledger **or** the saved details, not both. They can describe the same charge. Copies with the same ID and price count once. Conflicting prices are an input error. A receipt with a zero price is evidence. An absent receipt gives no price. The details format cannot tell you how many calls started, failed, or are missing. Local files do not authenticate the provider.

A trusted host that has the native attempt UUID, seat and returned generation ID can produce an ownership row with `examples.seatlab.workbench.generation_receipts.owner(trial, seat, generation_id)`. Store one JSON object per line in a private file. The row contains a domain-separated hash, not the raw generation ID. Duplicate delivery is safe. Conflicting owners are an error. Never match using the model name, time, cost, or seat label alone. The provider subtotal covers executions in the supplied packets. Executions from an open job can later be replaced. Retries absent from the packets and detached candidates are outside the subtotal.

### Capture the link on a future Dispatch Handoff job

The opt-in covers the native Dispatch Handoff `SeatAgent` with `policy=hosted`. Its host adapter makes the direct OpenRouter request. It records the native attempt UUID, the actual seat, and a hash of the response's top-level generation ID. It records no message, model output, key, raw generation ID, or charge.

Before a separately authorized native job, create a private directory on the host. Keep it outside the job, task, and all participant mounts. Use a local POSIX filesystem with file locking. Do not pass the setting into a container:

```sh
umask 077
mkdir -m 700 "$private_parent/dispatch-owners"
export SEATLAB_OPENROUTER_OWNERS_DIR=$(cd "$private_parent/dispatch-owners" && pwd -P)
```

The native host must use this checkout. An invalid directory prevents the generation request. If a response lacks a valid ID or its journal write fails, a valid decision still reaches the world. The host does not retry the request to obtain evidence. A recorded ID survives a later decision rejection. `owner_capture` in the seat metadata reports `recorded`, `no_generation_id`, or `write_failed` after a parsed response.

After the job, use `--owners "$SEATLAB_OPENROUTER_OWNERS_DIR/owners.jsonl"` with **one** local price source above. A new native retry has a new attempt UUID. A timed-out request may still cost money without returning a usable ID. The journal cannot price a call, count unseen calls, or enforce a budget.

Leaving the setting unset disables capture. The scripted `arrival` seat does not use it. The separate `run_panel.py` launcher filters it out. Stock Terminus-2 and other generic model adapters do not supply this link. A trajectory ID, a model label, or a matching timestamp is not a substitute. Read the saved prices without assigning a seat when no trusted link exists.

### Which workload incurred these charges?

You do not need a Harbor job to read a saved settlement ledger:

```sh
python3.12 -BS -m "$wb" account ledger "$events" --family plain --family v1 --routes
```

The default report hides model and provider names. `--routes` shows the requested model on each start and the model and provider on each settled receipt. The two tables describe the same calls. Do not add their totals. A provider can expand a model alias. Different labels alone do not prove fallback.

Each `--family TAG` selects local identities that start with exactly `TAG/`. The report groups other calls separately. A tag identifies a naming convention in this file, not a Harbor team. The command prints no full local identity, call ID or generation ID. Use `--json` for exact decimal subtotals. A missing receipt is not a zero charge.

For example, one saved cost-quality campaign has 280 started calls. Its 267 settled receipts report `$0.797539752`. The `plain/` group accounts for `$0.634926074` across 204 settled calls. The `v1/` group accounts for `$0.162613678` across 63. The other 13 starts have no settled price: 9 recorded failures and 4 without a recorded outcome. These are not subscription charges or an account-wide bill. None of these receipts establishes the cost of the separate native Harbor job.

As a cross-check, the generation-details reader reproduced that same subtotal from the 267 saved ID-and-price pairs. It assigned none to the unrelated native job. Putting the same prices in another format does not resolve the 13 unknown amounts.

## Decide whether a proposed run fits a budget

Use a saved native `lock.json`, an operator-supplied rate card and explicit **per-seat, per-attempt limits**. Use the exact route in the lock. No model is selected automatically. This command does not fetch prices. Price cache writes, long context, reasoning and fallback policies at the highest rate that could apply. Refuse to run if a route or limit is unclear.

```sh
python3.12 -BS -m "$wb" account plan "$lock" --expected-lock "$lock_sha256" \
  --rates "$rates" --limits "$limits" --declared-usd 1.00
```

`--expected-lock` takes `sha256:` plus 64 lowercase hex digits. The rate card uses USD per million tokens. For example, these are **illustrative**, not rates for a real model:

```json
{"schema":"harbor.rate-card/v1","currency":"USD","tokens_per_rate":1000000,"models":{"example/model":{"input_usd":"1.10","output_usd":"5.50"}}}
```

For each model seat, specify its exact `agent`, `model`, `max_calls`, `max_input_tokens_per_call`, and `max_output_tokens_per_call`. The limits file has schema `harbor.seat-limits/v1`, an integer `attempts_per_ticket`, and a `seats` object keyed by every registered seat. `attempts_per_ticket` includes initial execution and retries. For a registered `oracle` or `nop`, use just its `agent` and `"model":null`. An empty rate card is valid only for an all-scripted roster.

The native preview supports explicit `terminus-2` routes and the registered scripted seats. It rejects custom agents, route overrides and unsupported settings rather than guessing a price. The result identifies the exact rate card, limits file and lock or plan by SHA-256. Keep these digests with a spending decision.

An existing two-world shipping plan can be quoted per worker with `account shipping-plan STOCK_REG INFLIGHT_REG PLAN --expected-plan DIGEST --rates RATES --limits LIMITS --declared-usd USD`. See the [released worker assignments](../../ship_handover/disconnected-workers.md). Both commands exit 0 when within the declared amount, 1 when above it, and 2 when they cannot price the input. Quotes round the ceiling **up** to cents. A quote is conditional on external enforcement of its call, token, model and retry limits; it is not an enforced spend cap. An exit of 0 does not grant permission to start.

### Which controls can stop a charge?

| Path | What happens before a generation | What it cannot enforce |
| --- | --- | --- |
| [Direct Flash adapter](../visibility/deepseek.py) | Every participating process reserves a hold in the [same local SQLite file](../visibility/spend.py). A refused hold stops that request. An uncertain response keeps the hold. | A different ledger, a different host, code that bypasses the adapter, or a provider price change. |
| [Dispatch Handoff hosted seat](../seat_agent.py) | The request pins one free route, disables fallback and sets provider maximum prices to zero. | Any other OpenRouter client or another use of its key. |
| Native `account plan` or `account shipping-plan` | The operator calculates a conditional ceiling from the supplied rates and limits. No request occurs. | Any runtime limit. A quote is not a reservation. |

Reading old jobs and settlements uses none of these model routes. It needs no key. Do not start a paid native harness on the strength of a quote alone.
