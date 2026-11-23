# Can a Harbor team finish without a replacement booker session?

In the [four-seat experiment](peer-fanin.md), two bookers returned no verified answer. Their teams received native reward 0. One had three complete reports; the other had only two. We do not regrade either. We test a new, narrow scheduling policy on fresh worlds.

## Registration before the first attempt

**Question.** If the final model is unavailable, can a booker safely submit from the evidence already delivered to that seat? We test a known outage at the role boundary. We do not estimate the rate or cause of real outages.

Use the next four stock Flotilla calendars, 1216–1219, from the frozen 32-world screen. The booker rotates once through each physical seat. Use the same generator, seed, six attendees, booking contract, signed-in reporter model, and ordinary native verifier as the earlier study. Do not alter a grade, consult another seat's private calendar, or retry a missed attempt.

- Both arms ask all three reporters once, in the same cyclic order. Each reporter starts one independent tool-free session and sends its first answer through the stock service. Its seat checks that answer against **its own** calendar before adding the `exact` flag. The booker sees only its own feasible starts and the three reports delivered to its inbox.
- The four physical seats are real Harbor participants; only the reporters use a model. At the same booking boundary in both arms, the owner explicitly withholds the final model. This is an injected outage, not a provider claim. An outage must never be counted as a model session.
- **Abstain** leaves the booking unsubmitted. **Reduce** submits only if each of the three expected, distinct reporters supplied an exact report and intersection with the booker's own starts leaves one slot. An absent, malformed, inexact or ambiguous set cannot authorize a booking. The policy does not repair an existing model answer or invoke another session.
- Run both arms for each world. Run abstain first on even worlds and reduce first on odd worlds. Keep all eight first attempts, including a missing reporter or a failed native launch. Do not substitute the worlds from the motivating study.
- Before live reporters, run three typed controls: abstain with exact reports on 1216 (no submission, reward 0); reduce with exact reports on 1216 (one submission, reward 1); reduce with an inexact first report on 1217 (no submission, reward 0). These controls contain no model session.

**Pass for this four-world pilot:** all three typed controls match. All eight hybrid attempts have bound native outcomes, all 24 independent reporter sessions return, no model books, and every world changes from reward 0 without a submission to reward 1 with exactly one submission. A missing outcome or reporter makes the test inconclusive. If inputs and native outcomes are accounted for but a reward or a submission violates the rule, the test fails. Publish the native grades for incomplete attempts too.

The result cannot establish that fallback works for open-ended decisions, that a business agent should trust an unverified message, or that altering an old score is justified. The exact flag is produced by this task's local reporter adapter. Most tasks do not supply this fact.

## Result: four correct bookings without another model

**The registered pilot passed.** All eight native attempts finished in their registered order. Every reporter returned its full local list. None of the eight booking seats started a model session.

| Final seat policy | Native teams | Distinct reporter sessions | Client-reported reporter tokens | Bookings submitted | Correct native bookings |
|---|---:|---:|---:|---:|---:|
| Abstain | 4 | 12 | 83,645 | 0 | 0 |
| Reduce exact reports | 4 | 12 | 83,699 | 4 | 4 |

The three typed controls earned 0, 1 and 0, respectively. One inexact report prevented the reducer from submitting, even though all three reporters had replied. An independent check reconstructed the four successful intersections from the booking seats' actual requests. It matched each native submission. Neither the reducer nor a replacement model read the other calendars.

Use this policy for a decision that has a checkable local rule. Do not use it to guess an open-ended answer. This study injected an unavailable final model. It did not observe four real provider failures, reduce reporter costs, or change a previous grade.

Rebuild the [registered panel](results/join-recovery/panel.json) from the [published rows](results/join-recovery/rows.json). The command needs Python 3.12 and no account or Docker daemon.

```sh
d=examples/seatlab/results/join-recovery
python3.12 -BS -m examples.seatlab.fanin.join_panel "$d/rows.json" | cmp - "$d/panel.json"
```

For a new native control, use the [fork environment, historical generator and Docker slot setup](shared-read.md#run-one-team-on-your-own-machine). Choose a fresh absolute `OUT` outside the checkout.

```sh
export PYTHONPATH="$PWD:$PWD/src:$PWD/examples:$PWD/examples/seatlab"
"$DOCKER_SLOT" "$HARBOR_PYTHON" -B -m examples.seatlab.fanin.trial \
  --world 1216 --arm reduce --control correct --out "$OUT"
python3.12 -BS -m examples.seatlab.fanin.join_evidence "$OUT" \
  --world 1216 --arm reduce --tag control-correct
```
