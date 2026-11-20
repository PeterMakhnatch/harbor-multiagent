# Native speaker–listener results

The run measured **12 of 16 registered native Harbor trials**. In the 3 complete seed and delivery-order groups, the inventory model copied both revisions correctly. The dispatch model changed choices in both `reordered` groups, but did not always improve the number of optimal choices.

## What ran

The [frozen protocol](team-protocol.md) registers seeds `2001` and `2003`, 2 delivery orders, and 4 actor pairings. Each trial has 12 synthetic shipments. `fresh` delivers revision 2 only. `reordered` delivers revision 2, then revision 1. The first letter in each pairing identifies inventory (speaker); the second identifies dispatch (listener). `S` is the arrival-order script, not the oracle. `M` is `anthropic/claude-sonnet-5-5`, served by the first-party Claude CLI.

The run attempted **13 cells: 12 measured and 1 invalid**. It stopped at `s2003-fresh-MM`. The projection marks dispatch `invalid_or_refused_decisions`; it does not distinguish between those outcomes. The cell has no accepted measurement. The other `2003 / fresh` cells (`MS`, `SM`, `SS`) were not attempted. The run did not retry or replace any cell. Missing measurements do not count as zero scores.

## Complete pairs

Each entry shows **choices changed / change in optimal choices / change in feasible choices**, out of 12 shipments. The arrow shows the substitution from script to model. All 12 complete contrasts have equal delivered messages; 4 registered contrasts are unavailable.

| Seed / delivery order | Dispatch: `SS` → `SM` | Dispatch: `MS` → `MM` | Inventory: `SS` → `MS` | Inventory: `SM` → `MM` |
| --- | ---: | ---: | ---: | ---: |
| `2001 / fresh` | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| `2001 / reordered` | 4 / +4 / +2 | 4 / +4 / +2 | 0 / 0 / 0 | 0 / 0 / 0 |
| `2003 / reordered` | 5 / −1 / +3 | 3 / +3 / +3 | 0 / 0 / 0 | 4 / +4 / 0 |
| `2003 / fresh` | — | — | — | — |

- **Dispatch:** All `2001 / fresh` cells reached 12 optimal choices. In `2003 / reordered`, `SS` → `SM` gained 1 optimal choice and lost 2, even as feasibility rose from 9 to 12. With model inventory in that group, `MS` → `MM` raised both counts from 9 to 12.
- **Inventory:** Both actors matched their own native input on all 144 revision reports per actor. In `2003 / reordered`, `SM` → `MM` changed 4 choices with equal messages and the same accepted dispatch view digest. The 2 cells used separate dispatch model generations. This contrast does not show a speaker effect through the messages.

## Model resources

The table sums the CLI fields by role. CLI and API durations are sums across calls, not trial elapsed time.

| Cells | Model role | Calls | Input tokens | Cache creation tokens | Cache read tokens | Output tokens | CLI seconds | API seconds | CLI estimate (USD) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Measured | Inventory | 6 | 12 | 4,861 | 2,676 | 3,444 | 30.620 | 27.595 | 0.0541756 |
| Measured | Dispatch | 6 | 12 | 6,011 | 3,825 | 1,213 | 20.063 | 16.977 | 0.0365805 |
| Invalid | Inventory | 1 | 2 | 1,256 | 0 | 574 | 5.842 | 5.746 | 0.0107680 |
| Invalid | Dispatch | 1 | 2 | 1,510 | 0 | 381 | 3.337 | 2.905 | 0.0098540 |

The 6 measured script seats per role used 0 model turns. The run recorded 14 model turns: 12 in measured cells and 2 in the invalid cell. All 14 have a known CLI estimate, for a total of **$0.1113781 USD**. The protocol uses the existing Claude Max subscription with estimate limits of $0.30 USD per call and $4.80 USD total. The estimates are not an invoice or an incremental subscription charge. Local Docker instances did not incur a usage charge.

## Can stock Eval Lab read these trials?

Yes, through the raw trial reader. The [optional bridge](team_census.py) called stock `evallab.results.load_trial` on each attempted standalone trial. It found **13 native trial UUIDs, 26 seat records, and 12 known team rewards**. It did not duplicate the team reward across seats. The invalid trial has no accepted team score. The 3 unattempted trials have no seat token measurements.

The same bridge opened the stock census read-only on exactly those directories. All 13 census rows matched the raw path and trial UUID. None had a native job UUID, so the census correctly declined to project all 13 rewards. A standalone trial is not a Harbor job. The derived directory remained empty. This result does not describe trials launched under a native job.

| Native seat field | Inventory | Dispatch |
| --- | ---: | ---: |
| Seats with known input, cache, and output counts | 13 | 13 |
| Registered seats with unknown counts | 3 | 3 |
| `n_input_tokens`, summed over known seats | 8,807 | 11,360 |
| `n_cache_tokens`, summed over known seats | 2,676 | 3,825 |
| `n_output_tokens`, summed over known seats | 4,018 | 1,594 |

The native input field includes the CLI input, cache creation, and cache read counts. Do not add the cache column to native input. Totals include the invalid attempt's actual usage. See the [path-free census projection](results/native-model-census.json) for each registered slot. To reproduce on retained private trials, run `python examples/seatlab/team_census.py --study STUDY --evallab-root EVAL_LAB` with Eval Lab installed. No model, network service, or database backfill is needed.

## What the evidence supports

The report checks actor identity, served model metadata, accepted role-view digests, report fidelity, delivered messages, and committed choices. The independent native verifier scores feasibility and optimality without a model judge. These checks test the recorded model actions against deterministic evidence. They do not prove that a model understood its view or will repeat its choices. With 2 seeds, 1 missing group, and no repeated generation per seat, the results do not establish a general causal effect or rank models.

Sources: [machine-readable results](results/native-model-primary.json), [frozen protocol](team-protocol.md), [study driver](team_driver.py), [native model agent](native_model_agent.py), and [evidence and contrast code](team_report.py).

## What the 616 stored trials can tell us about team communication

We removed incoming peer messages from one receiver's recorded view at a time. This cost $0 and changed no original trial. A fetch counts only if the server returned a peer message to that receiver. “Before action” requires a later recorded action by **the same receiver**. We did not substitute another seat's action or count an unread post as a fetch.

| Population | Trials | Multi-seat trials | Trials with a changed fetch | Of these, before an action | Receiver interventions before an action |
| --- | ---: | ---: | ---: | ---: | ---: |
| [Flotilla](results/visibility-flotilla.json) | 276 | 180 | 83 | 81 | 142 |
| [Muster](results/visibility-muster.json) | 210 | 120 | 30 | 30 | 83 |
| [Crucible](results/visibility-crucible.json) | 130 | 124 | 122 | 122 | 303 |

The clearest contrast is within Muster. All 30 talking teams fetched a peer message before a later action. The 180 other trials had no team channel. In Flotilla, 89 trials made a message available, but only 83 recorded a fetch. The result depends on which event the recorder captures.

**None of these archives binds a peer message to an exact model request.** The counts show what the receiver fetched, not what the model used. No archived score estimates what would happen with a silent team. The [8 separate Crucible timeouts](results/visibility-crucible-timeouts.json) are outside the 616. Seven still recorded a peer fetch before an action. Dropping failed trials can drop evidence of team behavior.

The [replay instructions](replication.md#replay-the-original-server-evidence) include the expected archive roots and the adapter contract for a new task. The public results contain counts, not messages, seat IDs, or private trial paths.
