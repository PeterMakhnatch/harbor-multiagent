# Does reduced context change a model's choices?

In this small experiment, **no**. Claude Sonnet 5.5 chooses the optimal bay on all 24 original shipments with either input. Removing the superseded reports reduces canonical input size and the CLI reports fewer output tokens. This is a bounded observation replay, **not 4 native Harbor model trials**.

| Input to the dispatch model | Native source seeds | Optimal / decisions | Canonical input bytes | Reported input tokens, including cache | Reported output tokens | CLI cost estimate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Both reports as delivered | 2 | 24/24 | 3,902 | 3,413 | 811 | $0.021754 |
| Highest observed revision only | 2 | 24/24 | 3,014 | 3,029 | 356 | $0.015668 |

The first-party Claude Code CLI reports `claude-sonnet-5-5` as the served model in each response. Each generation receives only one canonical dispatch view. It runs in an independent session with no tools or project context. All 4 registered calls complete, in opposite arm order across the 2 seeds. The aggregate CLI estimate is **$0.037422**, not an invoice or evidence that the account incurred that charge. The CLI uses the existing first-party subscription. No GPU or paid container participates.

Both conditions have the same 24 underlying shipments and correct decisions. The sample is too small to rank models, estimate an accuracy difference, or attribute a latency improvement. A single paired model run cannot separate compression effects from output variability. The [registered protocol](context-experiment.md) and [machine report](results/first-party-context.json) include order, source trial UUIDs, view digests, local grades and usage fields.

A separate [primary native hosted arm](RESULTS.md) was unmeasured because the requested free route was unavailable under existing data settings. This experiment neither retries that route nor fills its empty cells. Model outcomes also differ from the scripted arrival-order control: that control changes 10 choices when it receives only the highest observed revision. That is a policy difference, not a contradiction.