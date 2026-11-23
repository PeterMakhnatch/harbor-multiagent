# When should a Harbor booker ask its three peers at once?

The [four-seat study](peer-fanin.md) tests whether the booker can *omit* a reporter. This experiment holds the number of sessions fixed. Does concurrent dispatch shorten the stock service's report interval without losing a correct booking? It measures a local deployment, not the model provider's internal concurrency or a general speedup.

## Registration before the first attempt

Use the next 8 consecutive worlds, 1208–1215, from the already screened stock Flotilla calendar cohort. The earlier structural screen was visible. No model trial from this block informed the choice of worlds. Keep the same commit, seed, seats, calendar shape, and rotating booker. Do not replace an unsuccessful attempt. The experiment runs one native Harbor team at a time, through the local Docker slot wrapper. We do not reserve the whole host or the service from other users.

- **Serial.** The booker requests the next reporter only after it receives the previous reply.
- **Parallel.** The booker issues all three requests without waiting for individual replies. It collects the replies in any arrival order. The book model sees them in the same fixed participant order as in serial.
- Every live team has three independent reporter sessions and one independent booker session. Each reporter sees only its own feasible starts. The reporter has no model tools. Its first answer travels through the stock message service. Neither harness supplies the booking answer.
- Run serial first on even-numbered worlds and parallel first on odd-numbered worlds. Alternate across worlds in index order. Use the same signed-in model selection as the earlier study. No paid model endpoint, no credit fallback, and no retries on an incomplete first attempt. Publish which attempts exist.
- Before live trials, run correct typed teams on world 1208 in both arms, and a wrong typed booking on world 1209 in parallel. Expect stock rewards 1, 1 and 0. These are controls, not model teams.

**Primary interval:** from the first booker request accepted by the stock service to the last report the service accepts. Also report the interval to the accepted booking and the native trial wall time. Count how many distinct requests the service accepted before its first report. Outstanding requests are not proof of overlapping model computation.

**Pass:** all 16 first attempts have bound native results and all three controls match. The median of the eight paired reductions `1 − parallel_ms / serial_ms` must be at least 25%. Parallel must be faster in at least 6 worlds. At least 2 requests must precede the first reply in at least 6 parallel teams. No world may switch from a correct serial booking to a wrong parallel booking. Missing attempts make the result inconclusive. Report rewards, tokens, sessions and both native intervals even if the pass test fails.

The [Replit systems report](https://frontier.replit.com/throwing-headcount-at-the-problem) motivates measuring wall time separately from score. It studied a different workload. Its numbers are not an expected effect size here.

## Result: request the three reports together on this workload

Concurrent requests shortened the observed gathering interval on **all 8 worlds**. The native service accepted all three requests before the first reply in every concurrent team. We reproduced the intervals independently from the stock service's send events. They do not measure how many models the provider ran at once.

| First-attempt measure | Serial | Parallel |
|---|---:|---:|
| Median first request to last report, 8 teams per arm | 34.61 s | 11.55 s |
| Median first request to booking, when submitted | 48.93 s (7) | 21.85 s (8) |
| Median native trial wall time, 8 teams per arm | 61.82 s | 35.25 s |
| Native correct bookings | 7/8 | 8/8 |
| Verified reporter sessions | 24 | 24 |
| Verified booker sessions | 7 | 8 |
| Client-reported tokens for verified sessions | 212,301 | 204,743 |

**The registered joint test was inconclusive.** The serial booker on world 1213 passed the subscription checks and recorded its request. It did not produce a verified reply or submit a booking. Harbor graded it 0. Its three reports still arrived in 30.014 s. We did not retry or infer why the reply was missing. This leaves 7 complete pairs for the original test. Their median paired reduction was 62.7%. Across all 8 measured report intervals, the descriptive median paired reduction was 64.6%. Neither calculation establishes a reward advantage.

All 3 typed controls matched (1, 1, 0). The sixteen live trials used 63 distinct verified model sessions. Each used the selected `gemini-4-carbon-low` route on a personal subscription. The service did not attest the served model. No paid API or credit fallback was used.

### Reproduce the result without starting a team

From this checkout, run:

```sh
d=examples/seatlab/results/gather-time
python3.12 -BS -m examples.seatlab.fanin.fanout_panel "$d/rows.json" | cmp - "$d/panel.json"
```

The [redacted rows](results/gather-time/rows.json) include every first attempt and typed control. The [panel](results/gather-time/panel.json) separates the registered statistics from `measured_intervals`. Missing events and wall-clock endpoints stay null. The command needs no Docker, model access or Harbor installation.

To collect a new private control, follow the [native setup](peer-fanin.md). Use a directory that does not exist. Do not reuse a study cell.

```sh
"$DOCKER_SLOT" "$HARBOR_PYTHON" -B -m examples.seatlab.fanin.trial \
  --world 1208 --arm parallel --control correct --out "$OUT"
python3.12 -BS -m examples.seatlab.fanin.evidence "$OUT" \
  --world 1208 --arm parallel --tag control-correct
```
