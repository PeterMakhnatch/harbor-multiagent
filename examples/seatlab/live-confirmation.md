# Does better team context improve Harbor reward?

None of the 3 original comparisons below established higher native reward across 48 new teams. A later receiver replay isolated a different question: [does the dispatcher use the bay IDs in a peer’s reports?](#do-the-bay-ids-in-a-peers-reports-help-the-dispatcher) It did. This is not a new team reward.

## Can a third agent choose between two proposed answers?

**Check whether either proposal can succeed before adding a reviewer.** On scheduling, not one of the 48 proposed times booked the meeting. On Dispatch, the reviewer recovered most of the available gain, but it did not reliably select the same proposal after we reversed their order.

Two real receivers had independently answered each of 32 original inputs. Where their answers differed, a fresh DeepSeek reviewer saw the same input and both proposals. We tested each display order twice. Identical proposals required no call. We scored the chosen proposal with the original task, not with a model judge.

| Measure | Scheduling | Dispatch |
|---|---:|---:|
| Sources with different proposals | 17/24 | 3/8 |
| Same proposal chosen with the same display order | 16/34 | 6/6 |
| Same proposal chosen after reversing display order | 36/68 | 8/12 |
| Correct units from a uniform choice, in expectation | 0/24 meetings | 34/48 shipments |
| Correct units with the reviewer, averaged over 4 selections | 0/24 | 35.5/48 |
| Highest possible grade from selecting whole proposals | 0/24 | 36/48 |

Neither task passed the [registered rule](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7061395832): 80% agreement after reversal and a grade no worse than a uniform choice. On Dispatch world `6804`, all 4 reviewers chose the second proposal on the screen. Reversing the order reversed the answer. The other 2 disputed Dispatch worlds always selected the better proposal. On scheduling, even repeated calls in the *same* order often disagreed. Variation alone does not establish an order bias.

The 80 new reviewer calls cost **$0.004044552**. All returned valid first replies. The comparisons share calls and cover only 3 scheduling worlds and 8 Dispatch worlds. These are receiver decisions on fixed team evidence, not new Harbor team rewards.

[Run the same check on your own two receivers](replication.md#let-a-third-agent-select-between-two-receivers) · [Per-source decisions, grades, and costs](results/public-peer-choice-live.json) · [Cost log](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7061479840).

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

An [earlier shipment study](https://github.com/PeterMakhnatch/harbor-multiagent/pull/66) also favored names: 38/72 versus 49/72 optimal decisions. It used six other worlds, with two repetitions each. We did not pool those results with the new worlds.

For the same changed-report notice on a second native task, see [Flotilla scheduling](calendar-notice-result.md).

The earlier [2/24 → 17/24 result](https://github.com/PeterMakhnatch/harbor-multiagent/pull/72) tested a different intervention. A scheduler saw or did not see two actual answers from its teammate. Those were replay decisions, not native team rewards.

## Two answers about meeting times

Each live Flotilla scheduler chose two times to ask about. Its teammate received both questions and sent its availability through the native message channel. The final scheduler saw either both answers or neither. It selected a meeting time in both arms.

| First-attempt result across eight calendar worlds | Answers withheld | Answers visible |
|---|---:|---:|
| Completed native teams | 8/8 | 8/8 |
| Teams that asked about the only mutually available time | 0/8 | 0/8 |
| Answers correct about the peer's availability | 10/16 | 15/16 |
| Teams that booked a mutually available time | 0/8 | 1/8 |

Only world 06 improved. The paired reward increased by **12.5 percentage points**, with seven ties and a one-sided **p = 0.5**. No team asked about the only time both participants could attend. The one successful team selected that time without receiving a positive answer about it. In the earlier replay, 6/12 dialogues asked about the shared time, and the very same dialogue fed both final conditions. Here every question and answer came from a new team. The two studies do not isolate a model or task difference.

The [native receipts](results/live-confirmation.json) include the 48 calendar calls. All settled at **$0.001984539**. Neither this result nor the shipment result below established that the answer effect transfers across tasks.

## Two answers about shipment eligibility

The dispatcher asked whether a specific bay was eligible for each of two shipments. Inventory answered both questions. Only the visible arm included the answers in the final decision context. The agents used a host-scoped channel for this exchange. The native verifier scored the bay choices against final inventory.

| First-attempt result across eight new worlds | Answers withheld | Answers visible |
|---|---:|---:|
| Completed native teams | 8/8 | 8/8 |
| Answers correct against final stock | 15/16 | 16/16 |
| Optimal shipment decisions | 25/48 | 22/48 |
| Mean native team reward | 0.5208 | 0.4583 |

The paired reward changed by **−6.25 percentage points**. One world improved, four declined, and three tied. The one-sided sign test for a gain gave **p = 31/32 (0.9688)**. All answers delivered to the final decision call were correct. That was not sufficient to increase team reward in this sample. This does not establish that delivering answers generally hurts.

**What failed after delivery?** In a post-hoc check, inventory said “no” to 11 visible questions. Dispatch still chose that exact bay for that shipment 7 times. All 11 negative answers were true. Seven of the 16 visible questions concerned a bay whose eligibility had changed. This locates a concrete failure without attributing intent. The next live test should ask whether showing a conflicting answer again before submission improves the native reward.

The [world and turn receipts](https://github.com/PeterMakhnatch/harbor-multiagent/blob/faf0a5bf61/examples/seatlab/results/dispatch-answer-transfer.json) cover 16 native teams and 96 choices. The [cost record](https://github.com/PeterMakhnatch/harbor-multiagent/pull/76#issuecomment-7058634259) accounts for all 64 calls: **$0.005766912**. No registered team was replaced.

## Do the bay IDs in a peer’s reports help the dispatcher?

**Yes, on these 8 native Dispatch worlds.** We kept each inventory submission, the dispatcher’s own utility tables, and its 2 opened revisions fixed. One arm showed the actual bay IDs. The other replaced only those IDs with `?`. Each shown prompt matched the one used by the original native dispatcher.

| Outcome from fresh first replies | IDs masked | IDs shown |
|---|---:|---:|
| Optimal shipments | 52/96 | 68/96 |
| Feasible shipments | 52/96 | 81/96 |
| Optimal among shipments with an opened revision | 14/32 | 22/32 |
| Optimal among shipments without an opened revision | 38/64 | 46/64 |

The model made 2 first replies per arm per world. Each reply covered 6 shipments. Showing IDs gained 16 optimal choices: 5 worlds improved, 3 tied, and none declined. The registered minimum was 12 choices. The exact test swapped both replies and all shipments together by world. Its one-sided p-value was **8/256 = 1/32**.

The 4 unopened shipments still had initial reports. The result does not show that their final revisions reached the receiver. A simple receiver that picked the highest-utility bay in the latest visible report scored 44/48 with IDs. The same receiver picked by utility alone with IDs masked and scored 26/48. These controls used one choice per shipment, not a new model draw.

**Harness implication:** preserve action IDs when shortening reports. A message count cannot tell a dispatcher which bay the sender listed. This test changed the receiver input only. It did not rerun inventory, measure live exchange, or establish a team reward gain. The [calendar replay](calendar-notice-result.md#did-the-receiver-use-the-peers-actual-start-ids) tests the same question on a different task. We did not pool their results.

[Registration](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7060559857) · [Native source and no-key controls](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7060590629) · [First-reply receipt](results/dispatch-peer-payload-replay.json) · [32 direct charges: $0.001948416 total, 0 holds](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7060705269).

To check the world test, token costs, and prompt commitments without credentials:

```sh
python3.12 -BS examples/seatlab/dispatch_payload_score.py --receipt examples/seatlab/results/dispatch-peer-payload-replay.json
```

## Give another receiver one real Dispatch input

You can use 8 native Dispatch worlds without the original trial folders, a Harbor install, Docker, or an API key. Start in this fork checkout with Python 3.12 or later:

```sh
work=$(mktemp -d)
python3.12 -BS -m examples.seatlab.dispatch_peer_case check
python3.12 -BS -m examples.seatlab.dispatch_peer_case prompt --world 6801 --arm shown > "$work/6801.shown.txt"
```

Give the receiver only the file contents. Use `Answer only with JSON.` as its system instruction. Use `--arm masked` to hide the peer’s bay IDs. The shipment aliases, utilities, read choices, and number of bays in each report stay fixed. Assign a different world to each worker (`6801` through `6808`). Keep the scorer and the fixture outside the receiver’s environment.

Save the receiver’s first reply unchanged, then score it in a separate process:

```sh
python3.12 -BS -m examples.seatlab.dispatch_peer_case score --world 6801 < "$work/6801.reply.json"
```

The reader checks the [public source commitments](results/dispatch-peer-sources.json) before it releases a prompt. For these 8 worlds, the public generator reproduces the inventory seat’s **actual submitted rows**, not just a plausible inventory. Both prompt hashes must also match. The score evaluates a new receiver against the stock task. It does not create a native team trial.

## Reproduce the comparison without a model

The [native receipt](results/live-confirmation.json) covers 48 distinct teams, 96 live seats, and 160 settled calls. The total token-priced cost was **$0.012457179**. The [process receipt](results/live-confirmation-process.json) gives question, answer, read, and action counts. Both files exclude private prompts and trial paths.

From a checkout of the fork's `experiments/seatlab-live-confirmation` branch, run:

```sh
python3.12 -S examples/seatlab/confirmation_summary.py
```

Add `--json` for exact fractions, paired world scores, and missing-reward bounds. The command reads the published receipt. It does not start containers, use an API key, or install a package. The bounds are not a confidence interval.

Each world contributed a new team per arm. Treatment ran first in alternate worlds. The order was balanced, not randomized. Each sign test assumes the two arms are exchangeable within a world under the null. Do not pool the tasks or treat shipments from one team as independent trials.
