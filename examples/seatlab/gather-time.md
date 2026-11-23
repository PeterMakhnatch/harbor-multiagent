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
