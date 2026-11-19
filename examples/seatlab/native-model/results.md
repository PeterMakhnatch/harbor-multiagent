# Native first-party cross-play: the dispatch role changes the result

On these 2 shipment seeds, replacing the last-arrival dispatch script changed which bays the team chose. Replacing only the inventory script did not change any accepted report. A second model did not add an observed gain over model dispatch alone.

| Inventory | Dispatch | Native worlds | Feasible shipments | Optimal shipments |
| --- | --- | ---: | ---: | ---: |
| Script | Script | 2 | 13 / 16 | 12 / 16 |
| Claude | Script | 2 | 13 / 16 | 12 / 16 |
| Script | Claude | 2 | 16 / 16 | 16 / 16 |
| Claude | Claude | 2 | 16 / 16 | 16 / 16 |

The [registration](https://github.com/PeterMakhnatch/harbor-multiagent/blob/8f75b57376/examples/seatlab/native-model/protocol.md) fixes the seeds, team order, budget and no-retry rule before generation. Stock Olam Harbor ran 8 separate worlds with 16 seat executions. Each world contains 8 shipments. The seeds repeat across team types; these are **2 distinct cases**, not 64 independent observations. The per-world rewards for script dispatch were 0.875 and 0.625. All 4 model-dispatch worlds scored 1.

## Evidence about the handoff

All 8 inventory seats submitted both required revisions for all 8 shipments. The same seed produced the same accepted inventory submission under every team type. All 128 accepted reports reached the corresponding dispatch view unchanged. The verifier independently reconstructs both role-local inputs and submissions from the world. All 16 seat receipts match. All 8 generation receipts name `claude-sonnet-5-5` on the first-party subscription; no container received credentials. This is evidence of the served identity and structured submission, not of the model’s reasoning.

The [public JSON](results.json) includes native UUIDs, role-local input and submission digests, scores, model identity, and per-generation usage. It contains no prompts, model prose, session IDs, credentials, or private paths. Unmodified `evallab.results.load_trial` resolves [all 8 trials](stock-eval-lab.json), 16 role executions, and the identical 8 shared rewards. The CLI reports $0.053282 of estimated token cost across all 8 generations. This is a subscription estimate, not an invoice.

## What the controls do and do not prove

On a private copy of the completed script-inventory/model-dispatch trial for seed 2003, we removed only the dispatch receipt. The native score remained 1. Its dispatch generation became unknown. Verified worlds fell from 8 to 7. No new trial or model call was involved. This checks that a passing native score cannot certify a missing role input.

Two small cases and one stochastic generation per model role cannot rank models or prove a general coordination advantage. The deterministic verification can be repeated. A fresh model generation need not repeat the public output. To collect new trials, read the [protocol](protocol.md) and `python collect.py --help`. Run `python analyze.py NATIVE_ROOT` on the resulting private run directory. The [frozen analyzer](https://github.com/PeterMakhnatch/harbor-multiagent/blob/0651bb5427/examples/seatlab/native-model/analyze.py) predates generation.
