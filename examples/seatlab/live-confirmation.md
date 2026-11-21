# Does better team context improve Harbor reward?

## Naming changed shipment reports

**The names changed what the team read. The native reward improvement did not pass the registered test.**

Each Dispatch Handoff team had two real DeepSeek seats. Inventory submitted two revisions for each of six shipments. Dispatch could open two current reports before selecting a bay for every shipment. Both arms saw the number of changed reports. Only the named arm saw which shipment reports changed. The final decision call saw the opened reports, not the notice.

| First-attempt result across eight worlds | Count only | Count and names |
|---|---:|---:|
| Completed native teams | 8/8 | 8/8 |
| Reads that opened a changed report | 6/16 | 16/16 |
| Reads where the best bay actually changed | 3/16 | 11/16 |
| Optimal shipment decisions | 31/48 | 36/48 |
| Mean native team reward | 0.6458 | 0.7500 |

The paired reward increased by **10.42 percentage points**. Five worlds improved, one declined, and two tied. The one-sided exact sign test gave **p = 7/64 (0.1094)**. The [registration](https://github.com/PeterMakhnatch/harbor-multiagent/pull/73#issuecomment-7058189600) required all eight pairs, a positive mean, and p ≤ 0.05. The study did not establish a reward gain. It did not establish equivalence either.

The arms used independent model calls on matched worlds. Neither inventory seat made a source-copy error. The 48 shipments per arm are not 48 independent teams. For a harness with a two-read limit, the evidence supports naming revisions to direct reads. It does not support promising a higher team score.

The [native receipt](https://github.com/PeterMakhnatch/harbor-multiagent/blob/0cfaab3f21/examples/seatlab/results/attention-confirmation-live.json) contains each world and turn. The [cost record](https://github.com/PeterMakhnatch/harbor-multiagent/pull/74#issuecomment-7058608137) lists each team's token-priced cost. All 48 calls settled at **$0.004705728** in total. No trial was replaced.

The earlier [2/24 → 17/24 result](https://github.com/PeterMakhnatch/harbor-multiagent/pull/72) tested a different intervention. A scheduler saw or did not see two actual answers from its teammate. Those were replay decisions, not native team rewards.

## Two answers about shipment eligibility

The dispatcher asked whether a specific bay was eligible for each of two shipments. Inventory answered both questions. Only the visible arm included the answers in the final decision context. The agents used a host-scoped channel for this exchange. The native verifier scored the bay choices against final inventory.

| First-attempt result across eight new worlds | Answers withheld | Answers visible |
|---|---:|---:|
| Completed native teams | 8/8 | 8/8 |
| Answers correct against final stock | 15/16 | 16/16 |
| Optimal shipment decisions | 25/48 | 22/48 |
| Mean native team reward | 0.5208 | 0.4583 |

The paired reward changed by **−6.25 percentage points**. One world improved, four declined, and three tied. The one-sided sign test for a gain gave **p = 31/32 (0.9688)**. All answers delivered to the final decision call were correct. That was not sufficient to increase team reward in this sample. This does not establish that delivering answers generally hurts.

The [world and turn receipts](https://github.com/PeterMakhnatch/harbor-multiagent/blob/faf0a5bf61/examples/seatlab/results/dispatch-answer-transfer.json) cover 16 native teams and 96 choices. The [cost record](https://github.com/PeterMakhnatch/harbor-multiagent/pull/76#issuecomment-7058634259) accounts for all 64 calls: **$0.005766912**. No registered team was replaced.
