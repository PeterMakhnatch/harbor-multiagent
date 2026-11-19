# A hard round limit without losing a feasible meeting

Five isolated Harbor participants scheduled a meeting in **24 of 24 registered native trials**, with at most 9 exchanges per trial. Every native choice, case digest and registered resource counter matched an independent calculation. A round limit did not require stopping with no answer. It did require a change in what participants could send.

## Native result

The [registration](https://github.com/PeterMakhnatch/harbor-multiagent/blob/f098e88f4bded49c9655bb26bd40c260be59c284/adapters/masbench-scheduling/hybrid-protocol.md) fixed the rule before launch. Each seat reports one candidate at a time. If 8 scalar rounds fail to resolve the case, each seat sends one bit mask of its remaining possible starts. The coordinator intersects the 5 masks. All 5 seats run in each trial. The new cases are 7–12 from each of 4 strata. They do not overlap the earlier 24 interactive cases. The dataset and its answers were public and previously examined.

The 24 trials used 185 exchanges. Nineteen cases needed the final mask exchange; 5 did not. The team sent 830 scalar replies and 95 suffix masks. Registered bodies contained 3,335 request bytes and 16,278 response bytes. All 24 choices were physically feasible and matched the published reference. There were no missing trials, invalid reports, or accepted nulls. The other 3 protocols below were **not** run natively on this panel.

## All 200 original scheduling cases: independent offline computation

| Protocol | Correct / 200 | Total exchanges | Worst case, exchanges | Total body bytes | Median body bytes per case |
| --- | ---: | ---: | ---: | ---: | ---: |
| Scalar cursor, no cap | 200 | 3,892 | 59 | 103,073 | 416 |
| Scalar cursor, stop after 8 | 47 | 1,432 | 8 | 29,737 | 157 |
| Send 5 complete hex masks | 200 | 200 | 1 | 198,000 | 990 |
| Up to 8 scalar rounds, then suffix masks | 200 | 1,585 | 9 | 160,482 | 1,021 |

The hybrid saved 2,307 exchanges relative to the uncapped cursor, but used 57,409 more bytes. It sent 37,518 fewer bytes overall than complete masks. Yet it used **more** bytes than complete masks in **145 of 200 cases**. A few early agreements account for the lower total. Choose the pure mask protocol when a single exchange matters. Choose the uncapped cursor when this body-byte total matters more than a round limit. The hybrid is useful under a 9-exchange bound; it does not dominate either design.

Bytes count the registered scalar or mask request and response bodies, including all 5 seats. They exclude envelopes, receipts, HTTP overhead and retries. No measurement here is a latency, token-cost, privacy or model-ability result. The bounded-scalar arm stops after **8 exchanges**; it is different from sending the first **8 starts** in one message.

## Reproduce without a model

Download the [native projections, offline case data and independent validation](https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/v0.6.0/native-calendar-hybrid-24.tar.gz). Archive SHA-256: `39a94c9a66c23fac72a4de1a42a138f98746fc2f316a33f0420ff0316ade2c52`. The native projections contain 24 distinct Harbor UUIDs. They are not the raw private trial directories.

From the [frozen adapter](https://github.com/PeterMakhnatch/harbor-multiagent/tree/f927fa63a1372f35e1bad5d92be82143ec5ae779/adapters/masbench-scheduling):

```sh
# Set DATA to the extracted evidence directory.
python3 hybrid_validate.py --offline "$DATA/offline-200.json" \
  --native "$DATA/native-A.json" "$DATA/native-B.json" --out "$DATA/check.json"
cmp "$DATA/check.json" "$DATA/validation-24.json"
```

To recompute all 4 offline arms, fetch [MASBench Scheduling at the registered revision](https://github.com/BUPT-GAMMA/MASBench/tree/c0301a650db95958924bb080192c09fef39389a5/Scheduling). Run `python3 hybrid_cost.py "$MASBENCH" --out "$DATA/new-offline.json"` with the dataset repository root. Compare the output with `offline-200.json`. The [dataset license](UPSTREAM-LICENSE.txt) travels with the evidence. The runner and model resource charge for these trials was $0.
