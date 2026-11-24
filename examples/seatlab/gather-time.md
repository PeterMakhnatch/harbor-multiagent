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

### Find where a team stopped

Use the saved [mailbox timelines](results/gather-time/mailbox-cuts.json) to separate a late report from a failed booking. This is a secondary analysis of the same first attempts. It does not change the original verdict.

```sh
wb=examples.seatlab.workbench
python3.12 -BS -m "$wb" mailbox report
python3.12 -BS -m "$wb" mailbox inspect 1213 serial
python3.12 -BS -m "$wb" verify stock-mailbox-flow
```

The world returned all 48 reporter replies in responses to the bookers' inbox requests across the 16 model teams. In world 1213, the serial journal shows all 3 replies in responses to booker `p2`. The journal has no booking. The separate native seat record says `p2:turn_failed`. This rules out a missing reply in the world's inbox. It does not establish what the booker read or why its turn failed.

`inspect` shows the first request, the first reporter inbox response containing it, the accepted reply, and the first booker inbox response containing that reply. An event number identifies the exact snapshot when several replies arrive together. Times start at the first accepted request. For a team that booked, the tool splits that interval along the last reply the world accepted. It does not add the times of reporters that worked in parallel.

### Check a workflow deadline without running a team

```sh
python3.12 -BS -m "$wb" mailbox deadline replies-returned --by-ms 20000
```

At this illustrative 20 s deadline, all 3 replies had reached the booker inbox in 8/8 parallel teams and 0/8 serial teams. This cutoff was not part of the registered test. The result is descriptive. It is not a model speed claim or a reason to cancel a future job.

Use `requests-returned`, `replies-returned`, or `booked`. Supply a deadline in milliseconds. A target at the deadline counts. To say it was missed, the journal must extend beyond the deadline without the target. If coverage ends at the same millisecond, another event could share that time. The monitor returns `coverage_ended`, not a failure. A missing first request or missing worker file also remains unknown. The monitor does not act on any seat.

For the 1213 serial team, the journal ends 30,977 ms after the first request. It proves the booker had not booked by 30,000 ms. It cannot establish what the world would show at 31,000 ms. The separate native grade remains 0.

### Return evidence from independent workers

Keep the native `artifacts/logs/world/events.jsonl` private. On the worker, export a compact cut. The default uses the published gather registry to verify the source digest and expected role order. `--rows` also accepts one verified gather row.

```sh
python3.12 -BS -m "$wb" mailbox export "$events" --rows "$row" > "$cut"
python3.12 -BS -m "$wb" mailbox audit "$events" --rows "$row" --cut "$cut"
```

For a new stock four-seat calendar outside these 8 worlds, supply the independently retained event digest and intended roles instead. The monitor supports the same one-request, one-reply gather protocol. It does not infer a roster, world, grade or model from a filename.

```sh
python3.12 -BS -m "$wb" mailbox export "$events" --sha256 "$pin" \
  --booker p1 --reporters p2 p3 p4 > "$cut"
python3.12 -BS -m "$wb" mailbox deadline booked --by-ms 30000 --cut "$cut"
```

A cut contains role labels, event numbers, elapsed milliseconds and the source digest. It contains no answer, message text, absolute time, path or model identifier. The digest can link the cut to another copy of the same file. `audit` reads the original once; it verifies every point, including the last observed event. A coordinator with only a cut can check its structure and consistency, but not its origin.

For the registered study, a coordinator can combine multiple returns in any order. It needs neither Harbor nor the private journals:

```sh
python3.12 -BS -m "$wb" mailbox report --rows "$release" \
  --cuts "$worker_a_cut" --cuts "$worker_b_cut"
```

`--rows` without cuts starts with no workers. The [full panel](results/gather-time/mailbox-panel.json) keeps all 16 planned model teams and 3 controls. It separates a missing row, a missing cut and an observed non-booking. Controls never count as model teams.

For `report` and `inspect`, exit `0` means all requested timelines were recorded; `3` means coverage is missing. For `deadline`, `1` means a known miss, `3` means unknown coverage with no known miss, and `0` means all measured targets were reached with no unknowns. Cohort deadline exits use only planned model teams. `audit` returns `1` for a different cut and `3` for an absent cut. All commands use `2` for invalid input.

To collect a new private control, follow the [native setup](peer-fanin.md). Use a directory that does not exist. Do not reuse a study cell.

```sh
"$DOCKER_SLOT" "$HARBOR_PYTHON" -B -m examples.seatlab.fanin.trial \
  --world 1208 --arm parallel --control correct --out "$OUT"
python3.12 -BS -m examples.seatlab.fanin.evidence "$OUT" \
  --world 1208 --arm parallel --tag control-correct
```
