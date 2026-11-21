# Do changed-report names help a live scheduling team?

**Naming changed weekdays did not improve native team reward.** Receivers opened more changed reports, but that difference did not meet the registered threshold.

| Outcome | Count only | Count + weekday names | Named wins / ties / losses by world | One-sided paired sign p |
|---|---:|---:|---:|---:|
| Teams that found the correct meeting | 1/12 | 0/12 | 0 / 11 / 1 | 1 |
| Opened reports that had changed | 10/24 | 15/24 | 6 / 4 / 2 | 37/256 (0.145) |

Each native team could open 2 reports. The senders produced different revisions independently. The largest attainable changed-read counts were 16 and 16, respectively.

## What changed

We ran 2 fresh direct DeepSeek teams in each of 12 fixed native Flotilla scheduling worlds. Each team had a sender and a receiver. The sender first saw 2 attendee calendars. It then saw all 3 and revised its weekday reports. Both receivers saw the old summary and the number of changed weekdays. Only the named arm saw which weekdays changed before requesting 2 reports. Neither arm saw a true answer from the verifier.

The native binary reward requires the unique joint 2-hour meeting start. The paired test requires all 12 worlds, a positive difference, and one-sided p ≤ 0.05. Reads are not separate trials. [Preregistered question and test](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7059349582).

## What this tells a harness builder

On [Dispatch](live-confirmation.md), naming reports moved changed reads from 6/16 to 16/16. Its team reward did not pass the paired test. The earlier 2/24 versus 17/24 result concerned showing actual answers in a receiver replay. It did not measure names or native team reward. Do not pool these tasks.

No sender reported its full week correctly. Across 24 revisions, 43 of 146 reported starts were valid. Senders missed 101 of 144 valid starts. The median report had 7 extra or missing starts. The receiver saw exactly 1 candidate in 8/24 teams. That candidate was correct in 3/8.

[Offline decision check](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7059540059): taking the sole visible candidate, when present, changed 3 failures to successes and 0 successes to failures. This reuses the same reports and decisions. It is not another live trial.

The true meeting remained in the visible candidates for only 4/24 teams. Only 1/24 final choices fit the receiver’s own calendar. [Checking for a consistent 1-hour or 1-day shift](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7059641483) raised that count to at most 4/24. This does not support a simple coordinate mismatch.

Test a private-calendar lookup tool against raw calendars next. Keep the notice and the 2-read limit the same. The tool must not reveal the other seat’s calendar.

These are fixed, previously studied worlds and one model. With truthful reports, 7/12 worlds already have one candidate before either read. The result does not estimate performance on unseen calendars.

## Verify without a model call

```sh
python3 examples/seatlab/calendar_notices/summary.py
```

[Redacted per-team evidence and direct token charges](results/calendar-notice-live.json) · [Frozen harness](https://github.com/PeterMakhnatch/harbor-multiagent/tree/0bd4b5563ea6df03d277cdc81c5e013bb33d2f34/examples/seatlab/calendar_notices) · [Native control evidence](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7059482347) · [Fixture checks](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7059394292).

Both the study reader and stock Eval Lab matched the IDs and rewards of 24 live teams. Four native controls returned 1, 0, 1, 0 without any model calls. Direct DeepSeek served 96 metered calls: 25,344 cached input, 27,194 uncached input, and 1,118 output tokens. Metered cost: **$0.004825932**. Unsettled reservations: 0.
