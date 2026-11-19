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

## What the evidence supports

The report checks actor identity, served model metadata, accepted role-view digests, report fidelity, delivered messages, and committed choices. The independent native verifier scores feasibility and optimality without a model judge. These checks test the recorded model actions against deterministic evidence. They do not prove that a model understood its view or will repeat its choices. With 2 seeds, 1 missing group, and no repeated generation per seat, the results do not establish a general causal effect or rank models.

Sources: [machine-readable results](results/native-model-primary.json), [frozen protocol](team-protocol.md), [study driver](team_driver.py), [native model agent](native_model_agent.py), and [evidence and contrast code](team_report.py).
