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

### Direct DeepSeek baseline

The only model credential is `DEEPSEEK_API_KEY`. Keep it in the key store. Do not place it in a task file or container.

```sh
/tmp/crucible-docker-slot.sh \
  keys run -- "$PYTHON" -B "$TASK/environment/world/capability_env.py" \
  "$PYTHON" -B "$TASK/run.py" deepseek "$OUTPUT_ROOT/deepseek"
```

The adapter uses `https://api.deepseek.com/chat/completions` directly. It requests `deepseek-flash`, disables provider thinking mode, and uses the original visible reasoning prompts. It makes no transport retries.

Each role has 5 scheduled model calls. The source's single format retry permits at most 10 calls per role, or 40 total. Each call permits at most 512 output tokens.

The serialized message guard is 30,720 UTF-8 bytes. The planning reserve allows 32,768 input tokens per call, including framing. Peak-price reservation is **USD 0.417792** for all 40 calls. It is not actual spend.

Before each paid request, the adapter checks the direct account balance. It stops new admission below USD 1.50 or after unknown usage. Balance and generation use a trial-scoped lock. Provider calls are serial; the round barriers remain synchronous. Do not interpret the baseline as a parallel-provider latency measurement.

The adapter records every response, format retry, token count, cache subset, price window, and cost. A failed started call without usage remains unknown. It does not become a zero-cost call.

The response model alias is not proof of a fixed provider build. Cost means the token-priced amount, not a wallet difference or invoice amount.

## Retained artifacts

Each run records its imported SDK and source root in `runtime.json`. Native results remain under `trials/`; the launch summary is `summary.json`.

Real-model records remain in each seat's `agent/deepseek.jsonl`. The world exports `/logs/world/agentsnet.json` for the verifier and task artifacts.

Keep raw role traces private. A public result should contain the source pins, complete trial count, both scores, elapsed time, token counts, actual cost, and any unknown usage.

## Observed real-model baseline

One registered DeepSeek trial runs on local Docker. No trial or transport retry occurs. All 4 native seats remain under test.

The executed source is [`18f8e8fbbb902b8fd4b13f254818c98c23332ea9`](https://github.com/PeterMakhnatch/harbor-multiagent/tree/18f8e8fbbb902b8fd4b13f254818c98c23332ea9). The later result entry changes only this README.

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

All calls start on Saturday. The token price uses the verified off-peak rates:

| Token category | USD per 1 million tokens |
|---|---:|
| Uncached input | 0.15 |
| Cached input | 0.003 |
| Output | 0.60 |

The source-parity proof compares the pinned prompts, parser, histories, routing, and both scores without a provider call. All 3 native controls also pass.

This is 1 successful episode, not an estimated success rate. Provider calls remain serial. The trial does not establish communication necessity or model superiority.
