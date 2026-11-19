# What a shorter report costs in 200 real scheduling cases

Each of the 5 Harbor participants reports its available starts. The coordinator selects the earliest start in all 5 reports. We changed only the report length: all available starts, or each participant’s first 8 starts. **Full reports schedule all 200 meetings. Short reports schedule 15.** The other 185 short runs completed and explicitly returned null. No registered trial is missing.

| Official answer count | Paired cases | Full: official | First 8: official | First 8: null |
| --- | ---: | ---: | ---: | ---: |
| 2 | 40 | 40 | 1 | 39 |
| 3 | 60 | 60 | 5 | 55 |
| 4 | 60 | 60 | 2 | 58 |
| 5 | 40 | 40 | 7 | 33 |
| Total | 200 | 200 | 15 | 185 |

There are 400 unique native trial IDs and 2,000 participant executions. All returned choices are physically feasible and official. These are the released MASBench Scheduling scenarios, not 200 people or model samples. Four cases also appeared in earlier smoke tests. The input data and offline results were already available. This is an integration and calibration study, not a blinded estimate.

## What independent workers can do

We transformed each full native decision into the 5 shortened reports. We gave 8 separate workers only `schema`, an opaque `request_id` and `view`. They received no recorded action, answer set, target input or score. A coordinator joined their results in reverse worker order. The merged predictions were byte-identical to the serial predictions (SHA-256 `8d0df3e7051c15b0d11c1712eda57def280df3877180e6feeae5d2928435447b`). All 200 input digests, actions and scores match the native short runs. The unchanged-input control has 0 matching inputs. Its 15 matching raw actions are coincidences, not reproductions.

A [separate DuckDB query](cohort-sql.md) uses only the 3 exported decision and registration files. It reproduced all 200 short decisions and the stratum counts above. It does not call Harbor or SeatLab. Both readers use the same underlying trials.

## Verify without Docker

Download the [public bundle](https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/v0.8.0/native-calendar-200.tar.gz). Its SHA-256 is `5717b49b4a0f52a7e8624ef60ae94f6d4d789173e0088d6cba202a549964a9fc`. Extract it into a new directory. With [SeatLab 0.8.0](https://github.com/PeterMakhnatch/harbor-seatlab/releases/tag/v0.8.0), run:

```sh
seatlab study verify study.json --expect-sha256 b140630534d5776c33b52a95aaaa6fc76eae0f73cf9161737541bbcad69ba5d9
```

The bundle includes the upstream license, all 400 public decision rows, the pair registration, the frozen predictions, and the independent SQL. The study verifies the 5 registered inputs to its 2 checks. The archive digest also covers the accompanying analyses and SQL. See the [native registration](cohort-protocol.md) and [pinned source data](https://github.com/BUPT-GAMMA/MASBench/tree/c0301a650db95958924bb080192c09fef39389a5/Scheduling).
