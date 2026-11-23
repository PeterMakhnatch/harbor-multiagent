# Can a Harbor booker leave an unneeded reporter idle?

In the [two-seat native study](shared-read.md), every receiver requested its only peer report and the neutral arm already scored 8/8. Four seats create a different question. If the stock task promises just one valid meeting, the booker may know it before the last reporter speaks. Does an operator need to open every model session?

## Freeze before inspecting any of these worlds

Use stock Flotilla `71ddf40b14ea81dd8eb0c32663e11abfe21cd1e3`, schedule/easy/talk, four physical seats, 600 seconds, seed `1709000311`, indices `1200..1231`. Do not replace failed worlds. Rotate the booker `p1,p2,p3,p4` by index. Request the three other seats in cyclic order after the booker. There is no scripted seat. A role sees only its own calendar, or, for the booker, reports it actually receives. Do not send the answer key to a seat.

**Opportunity screen.** Start with the booker's complete feasible two-hour starts. Intersect these with each truthful report in order. Record how many starts remain after 0, 1, 2 and 3 reports. The full intersection must equal the one stock answer in all 32 distinct worlds. A singleton reached earlier must be that same answer. Gather-all always uses 96 reporter calls. Admit a model comparison only if at least 12 of 32 worlds can skip a reporter, at least 16 of 96 reporter turns can be saved in total and at least 2 of the first 8 worlds can skip a reporter. All worlds count. If the screen fails, publish it; do not change seed, order or difficulty to obtain a live result.

**Conditional team comparison.** Use exactly indices 1200–1207 in both arms. Alternate arm order by world. In `all`, every reporter gets one independent subscription session. In `stop`, withhold later reporter sessions when the intersection of locally checked, complete reports and the booker's own starts is a singleton. A sender checks its actual first answer against only its own feasible starts. It never substitutes a corrected answer. An invalid or incomplete report disables early stopping. In both arms a fresh booker session chooses the final slot from the local list and the reports actually delivered. Use the same slot encoding, bounded task text and first-answer grammar in both arms. No model tools or generated code run. Native teams must go through the shared Docker slot wrapper. Do not count a reporter as saved if its model session was opened and only delivery was suppressed.

**Pass test.** Require all 16 first attempts accounted for, at least 2 fewer actual reporter sessions with `stop` across the 8 worlds, and no paired world where `all` scores 1 and `stop` scores 0. Name missing attempts, token counts and booked stock rewards separately. If fewer than 16 have evidence, the live comparison is inconclusive. This is a small resource-use pilot, not a non-inferiority or throughput claim. The 32-world screen has no model decisions or measured savings. The [AgentQuotaBench protocol](https://zenodo.org/records/24279036) motivates measuring demand; it does not report a comparison we replicate here.

## Result: four fewer reporter sessions, but no reward-preservation verdict

The [stock screen](results/peer-fanin/screen.json) found a unique answer in all 32 calendars. With exact reports, 15 worlds allowed at least one reporter to remain idle. The total opportunity was 16 of 96 reporter turns. Four of the first eight worlds had this opportunity. An independent calculation from the stock calendar files reproduced all 32 prefix sizes and answers.

All 16 native first attempts ran. Every reporter who answered returned its exact local list. The native intervals confirm the registered arm order for all eight pairs.

| On the same 8 worlds | Ask everyone | Stop at a certified singleton |
|---|---:|---:|
| Reporter sessions with a receipt | 24 | 20 |
| Booker sessions with a receipt | 7 | 7 |
| Correct native bookings | 7 | 7 |
| Native zeros without a booker answer | 1 | 1 |
| Client tokens on recorded sessions | 202,604 | 166,403 |

**Registered verdict: inconclusive.** On world 1202 the gather-all booker produced a request but no verified reply. On world 1204 the early-stop relay accepted the booker, but there was no model request. Neither team submitted a booking. Both native zeros remain in the [panel](results/peer-fanin/panel.json). All 14 teams with bound booker answers booked correctly. We did not repeat either failed attempt. We cannot infer the cause of either failure or claim equal quality, faster execution, or a lower bill.

Three separate native controls earned 1, 1 and 0. In the first, exact local reports allowed one reporter to stay idle. In the second, an incomplete first report forced the booker to ask all three reporters. In the third, a valid but wrong booking received the stock zero.

Rebuild the panel from the [redacted native rows](results/peer-fanin/rows.json). This needs Python 3.12, not Docker, an account, or any private prompt.

```sh
d=examples/seatlab/results/peer-fanin
python3.12 -BS -m examples.seatlab.fanin.panel "$d/rows.json" | cmp - "$d/panel.json"
```

To run a native control, set `HARBOR_PYTHON` to an isolated interpreter for this checkout. Use a local Docker slot wrapper and a new private result path outside the repository. The owner reader checks all four seat identities, per-seat prompts, stock messages, and the native reward.

```sh
export PYTHONPATH="$PWD:$PWD/src:$PWD/examples:$PWD/examples/seatlab"
"$DOCKER_SLOT" "$HARBOR_PYTHON" -B -m examples.seatlab.fanin.trial \
  --world 1200 --arm stop --control correct --out "$OUT"
python3.12 -BS -m examples.seatlab.fanin.evidence "$OUT" \
  --world 1200 --arm stop --tag control-correct
```

For a real team, replace `--control correct` with `--agy /absolute/path/to/agy` and use `--tag live` in the evidence reader. The supplied adapter requires the pinned personal subscription client, sufficient quota and zero fallback credits. A missing receipt is not a free or successful model call.
