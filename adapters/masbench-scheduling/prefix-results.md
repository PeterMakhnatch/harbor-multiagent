# Where a replay correctly predicts a changed action

**All 24 forecasts matched an independent five-participant Harbor run.** On 20 cases, the bounded coordinator agreed for 8 actions and then stopped at decision 9. On the other 4, both policies completed the entire negotiation with the same actions and inputs.

We [published the exact source-only forecasts](https://github.com/PeterMakhnatch/harbor-multiagent/blob/b70ba7759f121b874dcb878cadd0bc5e164e148e/adapters/masbench-scheduling/results/prefix-forecast.json) before starting any target trial. The [protocol](prefix-protocol.md) fixes the cases, registration, and treatment. The source outcomes were already known. This is not a blind benchmark or a result about learned agents.

| Registered cases | Independent pairs | Confirmed change at decision 9 | Confirmed full match | Missing or unconfirmed |
| --- | ---: | ---: | ---: | ---: |
| Development | 4 | 3 | 1 | 0 |
| Follow-up | 20 | 17 | 3 | 0 |

The alternative policy received 201 of the 497 source frames. The runner withheld 296 later views after an action changed. The 24 pairs had 181 equal input/action turns before their boundaries or completion. At every changed boundary, the independently executed native target received the same acknowledged input and submitted the predicted action. The offline replay did **not** predict the team's future score.

## What actually happened to the teams

Each arm used 24 distinct native trial UUIDs and 120 participant executions. The 48 trials used local Docker and no model. No trial was replaced. Only the coordinator's eight-round stopping policy changed.

| Group and native policy | Verified | Feasible reference meetings | Accepted nulls | Complete rounds | Scalar disclosures | Canonical payload bytes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Development, unbounded | 4 | 4 | 0 | 75 | 375 | 2,014 |
| Development, bounded | 4 | 1 | 3 | 28 | 140 | 604 |
| Follow-up, unbounded | 20 | 20 | 0 | 398 | 1,990 | 10,612 |
| Follow-up, bounded | 20 | 3 | 17 | 149 | 745 | 3,171 |

The bounded teams exchanged 8,851 fewer payload bytes in these 24 cases, but scheduled 20 fewer feasible meetings. This is not a network-latency, privacy, or model-token measurement. All 20 recorded nulls came from the budget rule, not exhausted calendars. Each of the 4 other trials reached unanimous replies before the budget stopped it.

## Reproduce without Docker

Use SeatLab v0.3.0 or later. Download `native-calendar-prefix-24.tar.gz` from [SeatLab releases](https://github.com/PeterMakhnatch/harbor-seatlab/releases). The archive SHA-256 is `82bc32706f60221ee0379627a7e5c82fa85257f31c21eadb4a0858e16660593f`. Extract it into a new directory. Run:

```sh
seatlab align source/episodes.jsonl target/episodes.jsonl --pairs pairs.json
python "$OLAM/adapters/masbench-scheduling/validate_prefix.py" \
  source target forecast.json --out new-validation
cmp validation.json new-validation/validation.json
```

The [checked per-pair results](results/prefix-native-validation.json) include both UUIDs, the source forecast, and the confirmation status. Their SHA-256 is `b0729ac12b20bb96fe8baf7b42ee79328132c2c117fd5f54674eb37c5c959209`. Native target measurements: [development](results/budget-smoke.json) and [follow-up](results/budget-followup.json). [Native source measurements](https://github.com/PeterMakhnatch/harbor-multiagent/blob/71f81e5a1634caa31cba4b650c90f53787fe5113/adapters/masbench-scheduling/interactive-results.md) come from a separate branch. The synthetic data retain the [MASBench MIT license](UPSTREAM-LICENSE.txt).
