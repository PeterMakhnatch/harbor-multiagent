# Dispatch Handoff: what actually ran

**Arrival order can undo a correct update without changing the world or the reporter.** On 2 fixed worlds, the last-arrival consumer discarded the benefit of the current report when an old report arrived after it. An evaluator that checks only reward cannot tell which report reached dispatch or which one it used. Here, we can compare the actual input sequence and the actual choice. We do not infer how a language model reasons.

## Primary native run

Each row covers the same 24 synthetic shipments: 12 per seed, 2 seeds. Each cell runs in real Docker with 2 native Harbor participants. The 5 revision-aware conditions use 10 trials. The 3 last-arrival conditions use 6 separate trials. These are paired shipments, not 192 independent trials.

| Policy | Context | Optimal choices / 24 | Feasible choices / 24 |
| --- | --- | ---: | ---: |
| Highest revision (stock oracle) | Silent | 12 | 12 |
| Highest revision (stock oracle) | Stale report | 14 | 17 |
| Highest revision (stock oracle) | Fresh report | 24 | 24 |
| Highest revision (stock oracle) | Fresh then stale | 24 | 24 |
| Highest revision (stock oracle) | Pooled truth | 24 | 24 |
| Last arrival | Stale report | 14 | 17 |
| Last arrival | Fresh report | 24 | 24 |
| Last arrival | Fresh then stale | 14 | 17 |

In the last-arrival policy, every reordered choice equals the stale-only choice for that shipment. Under reordering, the two policies disagree on 10 of 24 choices, 5 per seed. They agree on every shipment whose availability stays unchanged. In the 12 changed shipments, the stale report yields 2 optimal choices and 5 feasible choices. This is a result for a controlled synthetic task, not an estimate across warehouses, games or people.

**Finite reference:** under uniform enumeration of all 576 changed configurations, following the old availability is optimal in 1/3 and feasible in 1/2. This calculation has a different denominator from the sampled native trials. See `paired.py --design`.

## Held-out replay

Two new native panels use seeds `1001,1003`, 12 shipments and the `fresh,reordered` treatments. The 8 native trial IDs are distinct. The comparison matches all 4 paired cells on latent state, reconstructed role views, actual reports, decisions and scores. Both the inputs and actions match, not only the average reward. The historical actors did not acknowledge view digests: those values remain unknown. See [`results/replay.json`](results/replay.json).

## A model listing is not a measured model run

The primary model pilot planned 4 native trials on an explicitly free, provider-pinned route. All 4 native attempts have UUIDs. In every attempt, inventory requested one generation and dispatch requested none. The route returned HTTP 404. A separate route diagnostic identified the provider data policy. We did not change that policy or substitute another model. **0 of 4 planned trials yielded a model score.** No tokens, invoice amount or model ability can be inferred from that absence. The native CLI returned exit code 0; checking the nested outcome mattered.

## Reuse the result

The portable [primary bundle](results/primary.json) contains the complete planned grid, per-shipment actions, source hashes, native IDs and missing cells. The [held-out bundle](results/heldout.json) contains both independent panels. Run `export_study.py verify FILE` to check either bundle without Docker. These checks cannot authenticate the host or establish the private reasoning of any actor. Use the [native integration controls](baselines.md) to examine the genuine Eval Lab join and two runtime findings.