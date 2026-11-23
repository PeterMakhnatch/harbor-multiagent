# Can a recorded final receiver prompt stand in for a native trial?

Registered on November 23, 2026 before joining the native and replay grades across the released studies. Prior studies disclosed some within-study aggregates. This is a new comparison of existing real DeepSeek calls, not a blind prospective team trial.

**Operator decision.** When a change affects only a final receiver, can a worker use direct prompt replays to screen that change before buying native trials? Look for a specific warning: the native grade differs from replays more than the replays differ from one another. No absence of a warning proves equivalence or permits replacing a full-team confirmation.

**Arms and units.** Use Dispatch Handoff worlds 6801–6808: the native named dispatcher versus the 2 shown-payload and 4 shown-conflict replays of its final prompt. Use Calendar worlds 24–47: the native `lists` receiver versus the 2 `lists` replays of its final prompt. Each native team contributes one final receiver grade. Count the fraction of 6 optimally assigned shipments for Dispatch. Use the verified binary native team reward and exact-correct replay grade for Calendar. Do not combine the 2 tasks. Do not use masked, flagged or checked arms, retries, or altered first turns. Do not add worlds after looking at scores.

**Identity gate.** Require all 32 source trials, 32 native receiver grades and 96 settled direct replay calls. Bind native and replay rows by the original trial identity, its result digest and the receiver's user-prompt digest wherever the public receipts provide them. State exactly which link comes from the original exporter. Reject duplicate draws, missing or unsettled calls, unequal prompt hashes, out-of-domain grades and any incomplete world. Only the registered `deepseek-flash` request family is eligible. Report served model strings when available. A user-prompt hash alone cannot establish backend-version or whole-envelope identity.

**Statistic.** For each world compute the mean absolute grade difference between the native draw and each offline draw. Subtract the mean absolute difference over distinct pairs of offline draws. Give each world equal weight. Report both components, the signed mean grade difference (native minus replay), per-world values, and the fraction of native grades outside the range of the replay grades. Range coverage depends on the number of replays; do not compare raw rates between tasks.

**Pass/fail test.** Flag a task for native-only confirmation if its mean excess disagreement is at least 0.10 on the normalized grade scale and its upper-tail conditional permutation p-value is at most 0.025. Both requirements must pass. Treat each draw in turn as the native draw within its world, preserving all literal ties. Combine those finite null distributions exactly across worlds. The test assumes native labels are exchangeable within each world and label assignments across worlds are independent under the null. A common provider change or a shared source of world error can break those assumptions. No warning means **screen only**, not statistical equivalence. Missing identity evidence means **not evaluable**, never a zero grade.

**Cost.** No new model call, Docker run or provider access is needed for this audit. The outputs grade the receiver of a fixed transcript. They do not estimate how earlier seats would react to an intervention in a live team.

## Result: screen changes offline and confirm them in Harbor

All 32 registered worlds joined. The report contains 32 native receivers and 96 separate replay calls. Neither task met the warning threshold. On Calendar, 3 native grades fell outside both replay grades. A worker can screen a final-message change this way. Do not publish a team reward from fixed-prompt replays.

| Task | Worlds | Replays per world | Native mean grade | Replay mean grade | Native–replay disagreement | Replay–replay disagreement | Excess | Upper-tail p | Native outside replay range |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Dispatch | 8 | 6 | 0.7500 | 0.7431 | 0.0903 | 0.0917 | −0.0014 | 0.5421 | 0/8 |
| Calendar | 24 | 2 | 0.4167 | 0.3958 | 0.2292 | 0.2083 | +0.0208 | 0.5318 | 3/24 |

To reproduce the [exact results and source identities](results/receiver-replay-fidelity.json) from the fork root:

```sh
python3.12 -BS -m examples.seatlab.replay_fidelity examples/seatlab/results
```

No key, Harbor installation, or private transcript is required. Use `--task dispatch` or `--task calendar` for one study. The command rejects a changed evidence file instead of scoring it. The result lists each input hash, receiver draw, served model string, and exact fraction.

The public Calendar native export does not contain the user-prompt digest. That link comes from the original export on the private native host. The public Calendar replay grades link to settled calls by run name. Neither adapter proves that the system prompts, model weights or sampling settings were identical. A future native team can also change earlier messages. Those limits matter more than the small difference between the two means.
