# What helps two agents find a meeting?

**The local calendar check helped on the first 24 worlds. The transfer test did not establish that gain on 24 new worlds.** The check added no calendar information. Keep this distinction when choosing a default for a new workload.

## Does checking the received reports help a live team?

The receiver could open 2 revised weekday reports. It also kept the sender's old reports for the 3 unopened days. The `checked` prompt listed which reported starts still fit the receiver's own calendar. It did not add the other seat's calendar, unread revisions, or the target.

| Native outcome | Reports and local starts (`lists`) | Same format plus local check (`checked`) |
|---|---:|---:|
| Teams that booked the meeting | 10/24 | 20/24 |
| Teams whose received reports included the true meeting | 14/24 | 21/24 |
| Final choices that fit the receiver’s own calendar | 24/24 | 24/24 |

The check gained 11 paired worlds, tied 12, and lost 1. The gain was 41.7 percentage points. The exact two-sided sign test gave **p = 13/2048 (0.00635)**. This passed the [registered test](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7061523866). All 48 first teams finished. We replaced none.

Each team generated its own reports. The checked teams received better evidence more often, even though the sender's instructions did not change. This result measures the team workflow. It does not attribute all 10 extra bookings to the receiver's check.

The stock Eval Lab reader matched all 48 native scores. Independent checks matched 144 delivered messages and 192 accepted model turns. The 192 direct DeepSeek calls cost **$0.008619348** at the recorded token rates. No hold remains.

[Native results and every charge](results/calendar-visible-check-live.json) · [Cost log](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7061789256) · [Run both arms](replication.md#check-a-receivers-own-availability-against-delivered-reports) · [Reuse one of your own completed native trials](replication.md#compare-2-workers-on-your-own-completed-scheduling-trial).

### Does the booking gain repeat in fresh teams?

**Yes, on these 24 worlds.** We ran 2 new teams per world per arm. Each team generated its own messages. We reversed the starting arm between repetitions and replaced no team.

| Stock bookings | Reports and local starts | Same facts plus check |
|---|---:|---:|
| First repetition | 11/24 | 18/24 |
| Second repetition | 10/24 | 18/24 |
| Grade changed between repetitions | 7/24 worlds | 4/24 worlds |

The check gained **31.25 percentage points**. Across the 24 worlds, 11 improved, 11 tied, and 2 declined. The exact two-sided sign-flip test by world gave **p = 35/4096 (0.00854)**. This passed the [registered test](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7062119814). The true meeting appeared in the final reports of 33/48 unassisted teams and 36/48 checked teams. These are new teams, not new worlds or identical sender reports.

**Where did the teams differ?** The booking grade changed on 11 of the 48 same-workflow pairs. Only **3/11** had identical final receiver prompts. The other 8 pairs already differed in the evidence shown at the final turn. The [registered majority claim](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7062203777) did not hold. This comparison does not isolate the cause of a lost meeting. It shows why replaying only the last receiver cannot reproduce all variation in a team.

The stock Eval Lab reader matched all 96 native rewards. Independent checks matched 288 message deliveries and 384 model turns. All 384 direct DeepSeek calls settled for **$0.014291868**. There were no missing grades, replacements, or outstanding charges.

[All first attempts](results/calendar-native-repeatability.json) · [Matched turns, without message text or action hashes](results/calendar-native-repeatability-turns.json) · [Per-call tokens and costs](results/calendar-native-repeatability-cost.json) · [Run the experiment or inspect your own reruns](replication.md#repeat-a-native-team-without-replacing-its-first-attempt).

### Does the booking gain transfer to new calendars?

**Not established.** On 24 new worlds, 15/24 teams booked with the original lists and 18/24 booked with the local check. The gain was **12.5 percentage points**. Six worlds improved, 3 declined, and 15 tied. The exact two-sided sign test gave **p = 65/128 (0.508)**. The [registered rule](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7062759338) required at least 20 points and p ≤ 0.05. Neither condition passed.

These were 48 new two-seat teams, not receiver replays. The [stock admission check](results/calendar-transfer-48-71-intake.json) excluded role-specific calendar problems equivalent to the earlier worlds. The true meeting appeared in the final reports for 22/24 lists teams and 18/24 checked teams. Each sender made its own decisions. Do not use these unequal report counts to infer a receiver-only effect.

All 48 first attempts completed. The stock Eval Lab reader matched every native grade. Independent checks matched 144 message deliveries and 192 model turns. All 192 direct calls settled for **$0.008763804**. No replacement or outstanding charge.

[Run this on your own task block](replication.md#run-two-native-teams-on-each-world-in-your-own-calendar-block) · [48 first attempts](results/calendar-native-transfer-48-71.json) · [Costs by call](results/calendar-native-transfer-48-71-cost.json). The public reader recomputes the comparison without an API key:

```sh
python3.12 -BS -m examples.seatlab.calendar_notices.transfer_result summarize \
  examples/seatlab/results/calendar-native-transfer-48-71.json \
  --manifest examples/seatlab/results/flotilla-schedule-transfer-48-71.json \
  --sha256 8995d5311e9627989a1a35065902ef89a39f72eb693409532a95513b22a4a61a
```

### What if the receivers get exactly the same reports?

The check still helped. We used the 24 original `lists` teams. For each team, we matched its exact final receiver input, including the reports it actually received. We collected 2 fresh first replies per arm on each world. The check changed no underlying fact.

| Correct receiver decisions | Original input | Same input plus check |
|---|---:|---:|
| All 24 source worlds | 19/48 | 32/48 |
| True meeting appeared in the reports (14 worlds) | 18/28 | 27/28 |
| True meeting did not appear in the reports (10 worlds) | 1/20 | 5/20 |

The gain was 27.1 percentage points. Eleven worlds improved, 13 tied, and none declined. The exact two-sided sign-flip test by **world**, not by reply, gave **p = 1/1024**. This passed the [registered test](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7061629963). The two report-visibility groups are descriptive.

Equal inputs did not guarantee equal decisions. Across 2 replies, the selected slot changed on 10/24 original inputs and 8/24 checked inputs. The grade changed on 5/24 and 4/24, respectively. The stock key and the [single-trial scorer](replication.md#compare-2-workers-on-your-own-completed-scheduling-trial) agreed on all 96 grades.

**Practical use:** show the receiver which peer claims fit its own constraints. Preserve the source of each claim, especially when an unopened report can be stale. This result measures one model on scheduling. These 96 replies are not another 96 teams.

[Source and first-reply evidence](results/calendar-visible-fixed-replay.json) · [96 direct charges: $0.003303618](results/calendar-visible-fixed-replay-cost.json) · [Cost log](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7061830775).

### Should the receiver wait for the first report before requesting a second one?

**Waiting did not improve bookings on these senders.** We reused all 48 native senders from the 24-world test above. Each pair of fresh receiver branches shared its first requested day. The `immediate` receiver saw that report before choosing its second day. The `delayed` receiver did not. Both could read exactly 2 reports and used the same final booking format.

| Outcome on 48 fixed senders | Wait for the first report (`immediate`) | Request both before delivery (`delayed`) |
|---|---:|---:|
| Correct bookings | 28/48 | 30/48 |
| Final reports included the true meeting | 28/48 | 30/48 |
| Final reports identified the true meeting alone | 25/48 | 28/48 |

The two receivers chose different second days on 12/48 sources. Their sets of reported, locally feasible starts differed on only 3/48. Given the first day, **any** legal second request could change that set on only 23/48 sources. It could change whether the true meeting appeared on 19/48.

Across the 24 worlds, `immediate` won 0, tied 22, and lost 2. The difference was −4.2 percentage points. The exact two-sided world-level test gave **p = 0.50**. This did not pass the [registered test](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7061879200). It is not evidence that delayed reports work better. We measured no latency.

Of 288 logical turns, only 204 needed a model call. The 48 first requests and 36 identical bookings each used one physical reply across both branches. We counted no shared reply as independent evidence. No reply was invalid or missing. These are receiver paths against recorded senders, **not** 96 new native teams.

[Try it on your own native trial](replication.md#let-a-receiver-choose-which-report-to-read-next) · [48 paired receipts](results/calendar-staged-report-study.json) · [Source commitments and read opportunities](results/calendar-staged-report-sources.json) · [204 individual charges: $0.007254603](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7062069160).

## Held-out test: change both private calendar views

We ran 2 fresh teams on each world. Both teams got the same game, the same weekday notices, and the same 2-report limit. Only the calendar view changed. The first sender turn saw 2 attendees. Its revision and the receiver saw all 3 attendees at their own seat.

| Outcome | Raw busy hours | Exact local starts |
|---|---:|---:|
| Native teams that found the joint meeting | 0/12 | 6/12 |
| Sender gave an exact final list | 0/11 | 10/12 |
| Receiver chose a locally valid start | 1/11 | 12/12 |
| The received reports left only the correct start | 1/11 | 8/12 |

The exact view gained 6 worlds, tied 6, and lost 0. The one-sided paired p-value was **1/64**. This passed the [registered test](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7060106913). One raw sender returned an invalid revision. We kept its native score of 0; it had no receiver decision.

The receiver took the sole correct option in 6 of the 8 exact-view teams where the reports identified it. A [no-message policy](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7060302615) trained on 256 separate calendars got 4/12 using only local starts. The native test changed both seats together. It does not show how many wins required the peer’s messages.

The stock Eval Lab reader matched all 24 native scores. The 23 full conversations delivered 69 messages and matched 92 recorded turns. The [4 no-key native controls](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7060200350) scored as expected. Direct DeepSeek served 94 first replies for **$0.005690322**. No charge remained unsettled. These results cover 1 model, 1 paired draw, and 12 scheduling worlds.

[Native evidence](results/calendar-private-views-live.json) · [Per-team costs](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7060350368) · [Run the experiment](replication.md#test-private-calendar-views-with-2-live-seats) · [What other reads could reveal](results/calendar-read-opportunities.json).

## If only one seat gets the calendar helper, which one should it be?

**We did not establish a better role for the team.** We ran 24 new DeepSeek teams on the same 12 worlds. One seat saw its exact local starts. The other saw its original busy hours. Each team still had to agree on the one time that fit both calendars.

| Outcome | Sender alone got exact starts | Receiver alone got exact starts |
|---|---:|---:|
| Teams that booked the joint meeting | 0/12 | 3/12 |
| Sender gave an exact final list | 10/12 | 0/12 |
| Receiver chose a start that fit its own calendar | 2/12 | 12/12 |
| Actual reports left the sole correct option | 5/12 | 0/12 |
| Some legal choice of 2 reports could reveal the joint meeting | 10/12 | 1/12 |

Receiver-only help won 3 paired worlds, tied 9, and lost none. The two-sided sign test gave **p = 1/4**, above the [registered threshold](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7061145932). These were new teams, not unseen worlds.

The failure mode changed. With sender-only help, all 5 receivers missed the sole correct option when the reports identified it. With receiver-only help, all 3 successful teams chose a time their received reports did not identify. Changing which 2 reports to read could not fix 11 of the 12 receiver-only teams' missing evidence. A correct booking alone does not show that the receiver used a peer message.

The stock Eval Lab reader matched all 24 rewards. Independent checks matched 96 model turns, 72 message deliveries, and all 240 legal read pairs. The read pairs are alternatives within teams, not extra trials. The 96 direct calls cost **$0.004031520**. No hold remains.

[Native grades and all direct charges](results/calendar-role-views-live.json) · [Cost log](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7061329479) · [Reproduce the role experiment](replication.md#give-just-one-scheduling-seat-the-calendar-helper).

## Did the receiver use the peer’s actual start IDs?

**Yes, in a controlled receiver replay.** We froze the exact-view sender and the 2 reports its receiver opened in each of the 12 worlds. Fresh receivers saw either the real peer start IDs or `?` in their place. Both arms saw the same local starts, weekdays, entry counts, and instructions.

| Receiver outcome | IDs masked | IDs shown |
|---|---:|---:|
| Chose the joint meeting | 6/24 | 12/24 |
| Chose a start that fit its own calendar | 24/24 | 24/24 |

We ran 2 replies per arm per world. Showing the IDs gained 1 correct reply in each of 6 worlds and lost none. The one-sided test swapped labels by **world**, not by reply: 64 of 4,096 swaps matched or exceeded the observed gain (p = **1/64**). This passed the [registered test](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7060340046).

When the received reports contained the joint meeting, shown IDs gave 12/16 correct choices versus 6/16 with masked IDs. When they did not, both arms got 0/8. Two senders supplied no IDs at all; we kept those identical prompts. For this workflow, preserve actual proposed times, not just message counts. These 48 replies are not new native teams or native rewards.

[First-reply evidence](results/calendar-peer-payload-replay.json) · [Each direct charge: $0.001471896 total, 0 holds](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7060463024) · [Use the projection in another harness](replication.md#test-the-content-of-peer-reports).

The [Dispatch replay](live-confirmation.md#do-the-bay-ids-in-a-peers-reports-help-the-dispatcher) tests the same intervention on shipment bays. It also improves receiver decisions. Neither replay measures the team reward for masking IDs.

## Earlier test: name the changed weekdays

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

Could better report requests alone fix this? We checked all 10 legal pairs of weekday reads against each sender’s recorded reports. In **15/24 teams**, no pair would show the correct meeting among the receiver’s options. The actual reads showed it in 4 teams; alternative reads could show it in 5 more. This [offline diagnostic](replication.md#could-a-different-report-request-have-helped) does not rule out a correct guess.

## What helped the receiver?

We replayed each of the 24 final decisions with 2 fresh DeepSeek calls. One saw the original busy-hour calendar. The other saw the exact 2-hour starts for its **own** attendees instead. The peer reports, 2 reads, time grid, and final question stayed the same. Neither receiver saw the peer’s calendar.

| Replay outcome | Raw calendar | Exact local starts | Paired gains / ties / losses | One-sided world p |
|---|---:|---:|---:|---:|
| Fits the receiver’s own calendar (primary) | 3/24 | 22/24 | 19 / 5 / 0 | 1/1,024 |
| Fits both calendars (secondary) | 0/24 | 5/24 | 5 / 19 / 0 | 1/32 |

Both passed the [registered test](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7059949706). The list helped the receiver honor its own constraints. It did not establish better use of peer messages. In 3 of its 5 correct joint choices, the peer reports did not list the chosen start. A uniform choice from each exact local list would yield 4.46 joint successes in expectation. These are not new native team rewards.

An [independent check of 192 earlier live teams](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7059980058) shows why both roles matter. Moving a private lookup from sender to receiver raised receiver-valid choices from 21/96 to 66/96. Joint successes changed only from 17/96 to 20/96. Moving the tool changed what the sender could report, too.

[Replay data](results/calendar-presentation-replay.json) · [Run the offline check](replication.md#replay-one-receiver-without-changing-its-teammates) · [48 first replies and token charges: $0.002017524, 0 holds](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7060041258).

The reported experiment used 1 model and the 12 previously studied worlds. With truthful reports, 7/12 already have one candidate before either read. The result does not estimate performance on unseen calendars.

## Verify without a model call

```sh
python3 examples/seatlab/calendar_notices/summary.py
python3 examples/seatlab/calendar_notices/summary.py examples/seatlab/results/calendar-private-views-live.json
python3.12 -BS -m examples.seatlab.calendar_notices.role_summary
python3 examples/seatlab/calendar_notices/peer_payload_score.py --receipt examples/seatlab/results/calendar-peer-payload-replay.json
```

[Redacted per-team evidence and direct token charges](results/calendar-notice-live.json) · [Frozen harness](https://github.com/PeterMakhnatch/harbor-multiagent/tree/0bd4b5563ea6df03d277cdc81c5e013bb33d2f34/examples/seatlab/calendar_notices) · [Native control evidence](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7059482347) · [Fixture checks](https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7059394292).

For the original weekday-notice study, the study reader and stock Eval Lab matched all 24 native IDs and scores. Four no-key controls returned 1, 0, 1, 0. That study used 96 direct DeepSeek calls and cost **$0.004825932**. Its charges are separate from the two studies above.
