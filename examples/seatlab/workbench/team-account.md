# Account for a multi-agent Harbor job without running it

Start with the released data. Use Python 3.12 or later from this checkout. These commands do not install Harbor, run containers, contact a provider, or read API keys.

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

| Amount | Source | What it means |
| --- | --- | --- |
| Harness USD | Each native seat's `agent_result.cost_usd` | A reported subtotal with known and unknown seats. Other seats do not disappear when one has no price. |
| Token-priced USD | The released study's joined seat, turn and usage receipts | A calculation at the recorded rates. Input already includes cached input. Do not add cache a second time. |
| Provider USD | Settled generations in an explicitly supplied local ledger | A team subtotal only for exact request-to-seat matches. It is not all spend on the account. |

Do not add those columns. They can describe the same call. Keep JSON for exact source decimals. Digits beyond billing precision can come from the harness's serialized floating-point estimate.

### Hand off independently collected jobs

Each worker exports one stationary job. The receiver needs no access to its machine, model key, prompts or trajectories. These packets contain job and execution identifiers, seat labels and numeric evidence. Inspect labels before sharing them.

```sh
# Worker A; worker B exports its own job in the same way.
python3.12 -BS -m "$wb" account native "$job" --packet > worker-a.json
# Coordinator; a retransmission of the same packet is safe.
python3.12 -BS -m "$wb" account merge worker-a.json worker-b.json worker-a.json --json
```

An identical projection counts once, even if the JSON layout changes. Different snapshots of one job do not merge by timestamp. Select the authoritative copy. Two jobs that claim the same execution fail validation. Missing participants retain null costs. An entire worker that sent no packet is outside the report: the command cannot reconstruct an unseen job registration.

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

A trusted host that has the native attempt UUID, seat and returned generation ID can produce an ownership row with `examples.seatlab.workbench.generation_receipts.owner(trial, seat, generation_id)`. Store one JSON object per line in a private file. The row contains a domain-separated hash, not the raw generation ID. Duplicate delivery is safe. Conflicting owners are an error. Never match using the model name, time, cost, or seat label alone. The provider subtotal covers executions in the supplied packets. Executions from an open job can later be replaced. Retries absent from the packets and detached candidates are outside the subtotal.

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
