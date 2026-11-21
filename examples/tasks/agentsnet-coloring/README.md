# AgentsNet Coloring: released BA4 instance

This task ports 1 published AgentsNet graph-coloring instance into 4 native Harbor seats. It runs on local Docker.

Each seat controls 1 named node. It sees its own name, its immediate neighbors, and the total node count. It does not receive the full graph.

The seats exchange messages for 4 synchronous rounds. They then select 1 of 4 group labels. Adjacent nodes must select different labels.

## Published source

| Item | Pinned value |
|---|---|
| Benchmark | [AgentsNet: Coordination and Collaborative Reasoning in Multi-Agent LLMs](https://arxiv.org/abs/2507.08616) |
| Protocol and scorer | [`269f576e966f441e54ba72a653769663a4f4322c`](https://github.com/floriangroetschla/AgentsNet/tree/269f576e966f441e54ba72a653769663a4f4322c) |
| Dataset | [`disco-eth/AgentsNet`](https://huggingface.co/datasets/disco-eth/AgentsNet) |
| Dataset revision | `0e56e15cb7fc8fbea89c952f8053ccedf46c4a8e` |
| Released input | Train row 3: `ba`, 4 nodes, sample index 0 |
| Graph metadata | Graph seed 1825; naming seed 42 |
| Source license | MIT; the published dataset card also declares MIT |

The released row names the nodes Bradley, Sean, Hannah, and Kathryn. The task preserves that row. It does not generate a replacement graph.

The source defines the prompt text, message parser, 1 format retry, and individual conversation histories. The port preserves these rules. Messages reach only adjacent recipients in the next round. A seat can omit any neighbor from its message map.

The parser preserves the source's known-name allowlist and final-answer prefix behavior. Known non-neighbor destinations do not cause a format retry. Routing ignores them.

An unparseable second response becomes an empty message map or a null answer. The adapter never supplies a model decision.

This is a port of the released input and protocol. It is not a replay of a historical transcript or a full benchmark result.

## Native task boundary

`main` runs the authoritative world. `n0`, `n1`, `n2`, and `n3` are separate seat containers. All 4 seats are under test.

The world owns the full graph, round barriers, accepted actions, and scoring state. The normal seat image contains only its RPC client. It contains no graph fixture, oracle, scorer, or other seat's instructions.

Each trial creates fresh per-seat capabilities. The world derives the caller's identity from its capability. A request cannot select another seat through a body field or URL parameter.

The world does not publish other seats' prompts, histories, raw responses, or final answers. It rejects duplicate, stale, unauthorized, and terminal-state mutations.

A completed world remains final before grading. This task does not require PR #35's process-freeze fields. Final world state does not prove that every participant process has stopped.

Harbor main uses its public network mode for native seats. This task does not claim an egress restriction. The real-model adapter has no web or terminal tools for the model.

## Scores

The verifier writes 2 distinct metrics:

- `edge_fraction`: the original CLI score, from 0 to 1.
- `proper_coloring`: the paper's binary success condition.

An invalid or missing answer makes both scores 0. Otherwise, `edge_fraction` is the fraction of correctly colored edges. `proper_coloring` is 1 only when every edge passes.

The controls use the same world API and final scorer:

| Control | Expected edge fraction | Expected proper coloring |
|---|---:|---:|
| Oracle | 1.00 | 1 |
| All-Nop | 0.00 | 0 |
| Registered 1-edge conflict | 0.75 | 0 |

Controls are not model evidence. A successful model trial does not establish that communication is necessary. There is no single-seat comparison in this task.

## Run

Run these commands from the repository root. Install the repository's locked environment with `uv sync --locked` first.

```sh
TASK="$PWD/examples/tasks/agentsnet-coloring"
PYTHON="$PWD/.venv/bin/python"
OUTPUT_ROOT="$(mktemp -d /tmp/agentsnet-coloring.XXXXXX)"

"$PYTHON" -B "$TASK/run.py" oracle "$OUTPUT_ROOT/schema-check" --check
```

The schema check makes no Docker or model call. Each native command below creates exactly 1 trial in a fresh output directory.

On Peter's shared host, prefix every native command with `/tmp/crucible-docker-slot.sh`. This guard limits concurrent local Docker trials. It is not a package dependency.

```sh
/tmp/crucible-docker-slot.sh \
  "$PYTHON" -B "$TASK/environment/world/capability_env.py" \
  "$PYTHON" -B "$TASK/run.py" oracle "$OUTPUT_ROOT/oracle"

/tmp/crucible-docker-slot.sh \
  "$PYTHON" -B "$TASK/environment/world/capability_env.py" \
  "$PYTHON" -B "$TASK/run.py" nop "$OUTPUT_ROOT/nop"

/tmp/crucible-docker-slot.sh \
  "$PYTHON" -B "$TASK/environment/world/capability_env.py" \
  "$PYTHON" -B "$TASK/run.py" conflict "$OUTPUT_ROOT/conflict"
```

For a standalone host, use the inner capability-wrapper command with 1 active trial. The wrapper keeps capabilities in the child environment. It does not print them.

### Direct DeepSeek

The only model credential is `DEEPSEEK_API_KEY`. Keep it in the key store. Do not place it in a task file or container.

```sh
/tmp/crucible-docker-slot.sh \
  keys run -- "$PYTHON" -B "$TASK/environment/world/capability_env.py" \
  "$PYTHON" -B "$TASK/run.py" deepseek "$OUTPUT_ROOT/deepseek" \
  --max-output-tokens 512 --trial-budget-nanodollars 250000000
```

The adapter uses `https://api.deepseek.com/chat/completions` directly. It requests `deepseek-flash`, disables provider thinking mode, and uses the original visible reasoning prompts. It makes no transport retries.

Each role has 5 scheduled model calls. The source's single format retry permits at most 10 calls per role, or 40 total. The default output cap is 512 tokens. `--max-output-tokens` accepts 512 through 1024.

The serialized message guard is 30,720 UTF-8 bytes. Each POST reserves 32,768 input tokens at the peak uncached rate and the configured output cap at the peak output rate.

| Output cap | Peak reserve per POST (USD) | Peak reserve for 40 calls (USD) |
|---|---:|---:|
| 512 | 0.0104448 | 0.417792 |
| 1024 | 0.0110592 | 0.442368 |

These reserves are bounds, not actual spend.

The adapter allows up to 4 completion POSTs per trial and 1 per seat at a time. The round barriers remain synchronous. This is a configured maximum, not measured overlap or a speedup.

Before each POST, the adapter checks the direct USD balance against the USD 1.50 floor. The balance GET runs outside the trial lock. A successful check does not reserve wallet funds. Concurrent charges can lower the balance after a check.

The adapter records every response, format retry, token count, cache subset, estimated price window, and cost. A failed started call without usage remains unknown. It does not become a zero-cost call.

Reported cost uses the published rate schedule at the client start time. The estimate does not apply the Chinese public holiday exception. It does not establish the provider's billing clock, wallet debit, or invoice amount. The response model alias does not attest to a fixed provider build.

`--trial-budget-nanodollars` sets an optional shared trial limit. The example sets USD 0.25. The adapter checks capacity before the balance GET and rechecks it under the trial lock before reserving for the POST. Peak-priced charges, outstanding reserves, and the next peak reserve must fit the limit. A refused request does not start a POST.

For budget admission only, complete valid usage replaces its reserve with the published peak prices for actual uncached input, cached input, and output tokens. This upper bound does not depend on which price window the provider uses.

Unknown usage keeps the full reserve and stops new admission. A partial response does not establish usage, even if its captured bytes contain a usage field. A rejected balance check also stops new admission. Already admitted calls still settle when their client requests end.

Cancellation ends the client request and retains the full reserve when usage is unknown. It does not guarantee that the provider stops processing. A reserve is not a bill.

Each seat records the exact integer `budgeted_model_cost_nanodollars` for valid usage. In `call_finished`, that field is the call's budget charge, while `cost_usd` is its reported cost estimate. Unknown usage leaves both fields null and records the reserve separately.

`summary.json` labels `model_call_concurrency` as a `configured_maximum` and records the balance admission and cancellation policies. It still records exact `observed_model_cost_nanodollars` and `unknown_usage_reserve_nanodollars` totals. Native cost and token counts do not change. Missing accounting stays null. `--check` validates the cap and budget without a provider call.

## Retained artifacts

Each run records its imported SDK and source root in `runtime.json`. Native results remain under `trials/`; the launch summary is `summary.json`.

Real-model records remain in each seat's `agent/deepseek.jsonl`. The world exports `/logs/world/agentsnet.json` for the verifier and task artifacts.

Keep raw role traces private. A public result should contain the source pins, complete trial count, both scores, elapsed time, token counts, reported cost estimate, and any unknown usage.

## Observed real-model baseline

One registered DeepSeek trial runs on local Docker. No trial or transport retry occurs. All 4 native seats remain under test.

The executed source is [`18f8e8fbbb902b8fd4b13f254818c98c23332ea9`](https://github.com/PeterMakhnatch/harbor-multiagent/tree/18f8e8fbbb902b8fd4b13f254818c98c23332ea9). Reporting commit `103d7c3587d024512668a95acecc3fbbd663f4fc` changes only this README.

| Run measure | Observed value |
|---|---:|
| Edge fraction | 1.0 |
| Proper coloring | 1.0 |
| Native play | 96.767085 s |
| Native trial | 109.948753 s |
| Format retries | 8 |
| Responses at the output limit | 9 |
| Empty-message fallbacks | 1 |
| Calls with unknown usage | 0 |

Kathryn's round-3 message uses the source's empty-map fallback after 2 failed parses. No final answer uses a fallback. The result retains every response.

| Seat | Calls | Input tokens | Cached subset | Output tokens | Native wall time (s) | Token-priced USD |
|---|---:|---:|---:|---:|---:|---:|
| Bradley (`n0`) | 7 | 15603 | 13312 | 2472 | 92.546335 | 0.001866786 |
| Sean (`n1`) | 6 | 11155 | 9728 | 2497 | 96.758635 | 0.001741434 |
| Hannah (`n2`) | 6 | 11699 | 9856 | 2428 | 94.471291 | 0.001762818 |
| Kathryn (`n3`) | 9 | 24723 | 22272 | 3897 | 91.382557 | 0.002772666 |
| **Total** | **28** | **63180** | **55168** | **11294** | — | **0.008143704** |

Every paid call has a complete provider usage record. Native token totals and cost agree with those records.

Native usage lives in `seat_results`. The trial-level `agent_result` is null for native seats; it does not mean zero usage.

All calls start on Saturday. The stored cost estimate uses the published off-peak rates:

| Token category | USD per 1 million tokens |
|---|---:|
| Uncached input | 0.15 |
| Cached input | 0.003 |
| Output | 0.60 |

The source-parity proof compares the pinned prompts, parser, histories, routing, and both scores without a provider call. All 3 native controls also pass.

This is 1 successful episode, not an estimated success rate. This run uses serial provider calls. It does not establish communication necessity or model superiority.

## Repeated output-cap study

The registered question compares 512 and 1024 output tokens. Adoption requires fewer format retries without fewer proper colorings or excessive median cost and play time.

The study runs 6 fresh trials per cap. Pair order alternates, with 3 pairs in each order. The pilot above remains separate.

All 12 trials execute [`e56c801fcd72f5f453cf9996ba93aaf0d1e425c5`](https://github.com/PeterMakhnatch/harbor-multiagent/tree/e56c801fcd72f5f453cf9996ba93aaf0d1e425c5), with 4 under-test seats and serial provider calls. They do not execute the later peak-budget settlement described above.

| Cohort measure | 512 tokens | 1024 tokens |
|---|---:|---:|
| Complete trials | 6 | 6 |
| Proper colorings | 6 | 4 |
| Format retries | 47 | 8 |
| Length-limited responses | 49 | 8 |
| Invalid parses | 48 | 8 |
| Empty-message fallbacks | 1 | 0 |
| Final-answer fallbacks | 0 | 0 |
| Paid model calls | 167 | 128 |
| Total token-priced USD | 0.044996082 | 0.048567672 |
| Median token-priced USD per trial | 0.007271712 | 0.007779408 |
| Median native play time (s) | 85.6158775 | 83.7022665 |

**Keep the default at 512.** The larger cap meets the retry, fallback, cost, and time conditions. It fails the registered proper-coloring condition.

Cases `p01-cap1024` and `p05-cap1024` each have a valid edge fraction of 0.75 and proper coloring of 0. They are retained failures, not invalid rewards or runtime exceptions.

One length-limited 512-token response still passes the source parser. A length finish is not automatically a format failure.

### Cost and time per seat

Each cell is the median across that seat's 6 trials. Seat intervals overlap. Do not add them to obtain trial wall time.

| Seat | USD, 512 tokens | USD, 1024 tokens | Wall time, 512 tokens (s) | Wall time, 1024 tokens (s) |
|---|---:|---:|---:|---:|
| Bradley (`n0`) | 0.002241048 | 0.002549244 | 81.0594055 | 81.6069325 |
| Sean (`n1`) | 0.001633260 | 0.001357164 | 82.7266495 | 80.1795240 |
| Hannah (`n2`) | 0.001709001 | 0.002132997 | 83.8606875 | 82.6364665 |
| Kathryn (`n3`) | 0.001845495 | 0.001872099 | 83.8975035 | 80.6499710 |

The complete cohort has 295 paid calls and costs USD 0.093563754 from raw usage and the published off-peak tariff. No usage is unknown. No trial or transport retry occurs.

The cohort uses 1 released graph. These counts do not establish a benchmark-wide success rate or a causal quality loss from the larger cap.

The cohort SHA-256 is `286848f036f0c8ea1cbe6b4387eb6d4d305afc62ab07694c6083ae22077b445b`. The observed source snapshot has SHA-256 `900cd00a1b87d50b7145616698b550bc8d936f5bb74f1d8307310bb54c0c3f30`.

## Native concurrency and cancellation study

The study executes agent commit `cd7f32cbf3c16b957ca2ea3a62d95cb78cb0567d` with SDK commit `6aa0f2c653f15e2578a54aab604db6686e589b89`.
It registers 6 clean trials and 6 cancellation trials before any model call.
Every trial uses 4 under-test seats, 512 output tokens, and no Trial or transport retry.

All 6 clean trials show 4 physical request bodies complete before the first response-header read completes.
The clean condition uses no barrier, pause, or artificial delay.
All 6 native rewards remain valid. Five trials produce a proper coloring.
Case `clean-03` has a valid proper-coloring score of 0 and remains included.

Each cancellation probe pauses header consumption only after the real request body reaches the transport.
The probe cancels the native Trial once after all 4 bodies finish writing.
All 6 native `Job.run` calls raise `CancelledError`. They do not return a successful Job result or receive an official reward.
Across all 12 trials, every client scope exits before `AGENT_END`. Admission counters remain stable through native `END` and Job exit.

### Measured cost and time per seat

Each seat has 6 clean intervals and 6 cancellation intervals.
The cost column sums complete clean usage. Time columns show medians of native seat execution intervals.

| Seat | Clean calls | Known clean USD | Clean time (s) | Cancellation time (s) | Unknown cancelled calls | Held reserve USD |
|---|---:|---:|---:|---:|---:|---:|
| Bradley (`n0`) | 51 | 0.014248506 | 37.3500630 | 0.6676070 | 6 | 0.062668800 |
| Sean (`n1`) | 37 | 0.009324732 | 35.3405520 | 0.6611485 | 6 | 0.062668800 |
| Hannah (`n2`) | 47 | 0.012588180 | 35.4327095 | 0.6549755 | 6 | 0.062668800 |
| Kathryn (`n3`) | 50 | 0.014012898 | 35.1477415 | 0.6478660 | 6 | 0.062668800 |

The 185 complete calls report 429390 input tokens, including 383872 cached tokens, and 70325 output tokens.
All complete calls start in the UTC weekend off-peak window.
Their token-priced known subtotal is USD 0.050174316:
`(429390 - 383872) * 0.15 / 1000000 + 383872 * 0.003 / 1000000 + 70325 * 0.60 / 1000000`.

The 24 cancelled calls have no complete usage. Their fee stays unknown.
The ledger retains USD 0.250675200 for them. A held reserve is not a bill or a refund.
Peak-priced known charges plus held unknown reserves total USD 0.351023832, below the registered USD 0.40 cap.
The complete cohort bill remains unknown.

Median native `Job.run` time is 49.380030750 seconds for clean trials and 12.1527616455 seconds for cancellation trials.
These monotonic intervals exclude the Docker slot wait, `Job.create`, and archive checks.
Seat intervals include model and world waits. They overlap and must not be added as Job wall time.

The local SDK archives preserve native result bytes but omit the seat trace directories at the executed SDK revision.
The study keeps separate private trace copies and hashes. It does not claim a Hub transfer or recovery of omitted logs.
Client joins do not prove provider compute overlap, server cancellation, a refund, or the absence of every container process.
The study does not establish a causal speedup, a quality change, or a benchmark-wide success rate.

The scoped gate passes 192 budget scenarios across 32 recorded seeds, Ruff, formatting, and the agent/runner type check.
The actual public `--check` mode reports a configured concurrency of 4 without a model or Docker call.
Independent verification has SHA-256 `e1c104cacff1aaf619465bedc67047fd897ad13481c6b86e82257ee01793b052`.
