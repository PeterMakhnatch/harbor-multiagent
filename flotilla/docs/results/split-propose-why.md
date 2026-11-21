# Why the Split proposal trials did or did not reach a deal

**2 of 5 trials reached a deal.** Both deals came in round 1. The strong seat proposed taking its outside option or 1 point more. The weak seat countered, then accepted the strong seat's original offer. Across the 3 failures, there were 25 posted proposals and no successful acceptances. There was 1 acceptance attempt: it arrived after the only offer the recipient tried to accept had expired.

**Coverage:** All 5 finished `sp5` trials, instances 0–4. All had valid scores. Instance 5 did not run because of the budget guard. In this arm there are no free-text messages and each seat knows only its own outside option. A seat can see and accept the other seat's proposals until the round closes. A claim or pass does not itself reject a proposal, but the second claim or pass can close the round immediately.

## Every posted proposal

Splits and gains are **p1 / p2** in points. Gain is the seat's proposed points minus its own outside option. The quote is from the **recipient** trajectory, with its `step_id`. “Ignored” means no uptake or offer-specific decision was recorded. It does not establish that the recipient saw the offer and chose to ignore it. “Crossed” means both sides posted but the recipient's next decision did not address the other split live. “Rejected” means the model ruled it out in its reasoning, not that it used a reject action. IDs encode the round, and the table follows the world event order within each trial.

### Instance 0 — `sp5-786a0e` — pot 67; outside options 9 / 44

| Round | ID | Proposer | Split | Gain | Recipient response and evidence |
|---:|---|---|---:|---:|---|
| 1 | 1-1 | p2 strong | 23 / 44 | +14 / 0 | **Countered, then accepted.** p1 s4: “A compromise offer of p1=32/p2=35 may be acceptable to p2 and is better for us than accepting p2's current 23.” p1 s8: “To avoid losing the round entirely, I should immediately accept p2's proposal, which gives p1 23 points.” |
| 1 | 1-2 | p1 weak | 40 / 27 | +31 / −17 | **Rejected.** p2 s7: “P1's proposals give us only 27 or 35, both below our outside option of 44, so accepting them would reduce our score.” |
| 1 | 1-3 | p1 weak | 32 / 35 | +23 / −9 | **Rejected.** p2 s7: “We should not accept either.” |

### Instance 1 — `sp5-22c14b` — pot 140; outside options 96 / 13

| Round | ID | Proposer | Split | Gain | Recipient response and evidence |
|---:|---|---|---:|---:|---|
| 1 | 1-1 | p2 weak | 70 / 70 | −26 / +57 | **Crossed; no live review recorded.** p1 s3: “I will submit two proposals: a safe 96/44 split that covers any opponent outside option up to 44, and a more aggressive 118/22 split to capture extra value if the opponent's outside option is low.” |
| 1 | 1-2 | p1 strong | 118 / 22 | +22 / +9 | **Ignored; expired before p2's next read.** p2 s6: “We are now in round 2 with no proposals.” The offer was live for 0.40 s. |
| 1 | 1-3 | p1 strong | 96 / 44 | 0 / +31 | **Ignored; expired before p2's next read.** p2 s6: “We are now in round 2 with no proposals.” The offer was live for 0.21 s. |
| 2 | 2-1 | p1 strong | 97 / 15 | +1 / +2 | **Tried to accept; expired.** p2 s10: “Waiting risks receiving only 13 or a worse future split, so accepting now guarantees a score of 15.” The POST arrived 9.04 s after the round closed. |
| 2 | 2-2 | p1 strong | 100 / 12 | +4 / −1 | **Rejected.** p2 s10: “There is maybe proposal 2-2 gives 12 (< outside).” |
| 4 | 4-1 | p2 weak | 40 / 31 | −56 / +18 | **Ignored under a standing refusal.** p1 s9: “No claim deal or proposal in this or any later round can improve our score.” p1 s15 recognized the split later: “Round 4 p2 proposed 40/31 but we passed so cannot accept?” |
| 6 | 6-1 | p2 weak | 15 / 29 | −81 / +16 | **Rejected.** p1 s22: “p2 has proposed a 15/29 split, which is far below our 96-point outside option, so we must not accept it.” |

### Instance 2 — `sp5-fbf4b5` — pot 133; outside options 19 / 78

| Round | ID | Proposer | Split | Gain | Recipient response and evidence |
|---:|---|---|---:|---:|---|
| 1 | 1-1 | p1 weak | 73 / 60 | +54 / −18 | **Ignored; no live read recorded.** p2 s6: “p2's claim was not registered before the round closed.” Its intended 28 / 105 was never posted: it reached round 2 and exceeded the smaller pot. |
| 2 | 2-1 | p2 strong | 25 / 80 | +6 / +2 | **Crossed; no live review recorded.** p1 s15: “I will offer p2 75 and claim 70 as a fallback.” The last p1 state read preceded both offers. |
| 2 | 2-2 | p2 strong | 28 / 78 | +9 / 0 | **Crossed; no live review recorded.** p1 s15: “Post a round-2 proposal split p1=31, p2=75.” p1's next state read was in round 3. |
| 2 | 2-3 | p1 weak | 31 / 75 | +12 / −3 | **Ignored; expired as p1 claimed.** p2 s11: “If p1 makes a proposal, we can accept it before the round closes if it gives us at least our outside option of 78.” No live p2 read followed the offer. |
| 3 | 3-1 | p2 strong | 0 / 84 | −19 / +6 | **Rejected and countered.** p1 s19: “p2 has posted lowball proposals giving p1 only 0 or 5, which are worse than our outside option of 19.” |
| 3 | 3-2 | p2 strong | 5 / 79 | −14 / +1 | **Rejected and countered.** p1 s19: “We should not accept them. A counterproposal offering p2 34 (p1=50) is a strong but plausible deal” |
| 3 | 3-3 | p1 weak | 50 / 34 | +31 / −44 | **Ignored; expired as p1 claimed.** p2 s17: “Our two proposals are active, but p1 has not yet responded.” The next recipient step was in round 4. |
| 5 | 5-1 | p1 weak | 33 / 20 | +14 / −58 | **Ignored under a standing refusal.** p2 s22: “We should pass to block any claim-based deal and not accept any proposal.” The offer expired as p1 claimed. |
| 6 | 6-1 | p1 weak | 21 / 21 | +2 / −57 | **Ignored under a standing refusal.** p2 s24: “We should pass to block any claim-based deal and not accept any proposal.” The offer expired as p1 claimed. |

### Instance 3 — `sp5-effb29` — pot 96; outside options 7 / 57

| Round | ID | Proposer | Split | Gain | Recipient response and evidence |
|---:|---|---|---:|---:|---|
| 1 | 1-1 | p1 weak | 48 / 48 | +41 / −9 | **Rejected and countered.** p2 s5: “p1 has proposed an even 48/48 split, but our outside option is 57, so accepting that would be worse than no deal.” |
| 1 | 1-2 | p2 strong | 38 / 58 | +31 / +1 | **Countered, then accepted.** p1 s8: “Posting a counteroffer such as p1=44, p2=52 may improve our outcome” and p1 s9: “Accepting p2's offer locks in 38 points, which is much better than the outside option of 7 and avoids pot shrinkage risk.” |
| 1 | 1-3 | p1 weak | 44 / 52 | +37 / −5 | **Ignored; no live p2 read after posting.** p2 s7: “We need to wait for p1 to act or for the round to close.” p1 accepted 1-2 while that wait was pending. |

### Instance 4 — `sp5-5d3f3b` — pot 68; outside options 12 / 44

| Round | ID | Proposer | Split | Gain | Recipient response and evidence |
|---:|---|---|---:|---:|---|
| 1 | 1-1 | p1 weak | 36 / 32 | +24 / −12 | **Rejected; attempted counters did not post.** p2 s4: “Our outside option is 44, so accepting would be below our fallback.” Its claim closed the round before either counter could post. |
| 2 | 2-1 | p2 strong | 10 / 44 | −2 / 0 | **Crossed; not inspected live.** p1 s13: “p2 may have posted two proposals before we acted, but the round closed before we could inspect them.” p1 had proposed 27 / 27. |
| 2 | 2-2 | p2 strong | 9 / 45 | −3 / +1 | **Crossed; not inspected live.** p1 s13: “the round closed before we could inspect them.” p1's claim closed the round. |
| 2 | 2-3 | p1 weak | 27 / 27 | +15 / −17 | **Ignored; expired as p1 claimed.** p2 s8: “We should monitor for any acceptance or p1 proposal before the round closes.” The next p2 read showed round 3. |
| 3 | 3-1 | p1 weak | 13 / 30 | +1 / −14 | **Ignored under a standing refusal.** p2 s9: “Any deal this round would yield us at most 43, worse than no deal.” The offer expired as p1 claimed. |
| 4 | 4-1 | p1 weak | 13 / 21 | +1 / −23 | **Ignored under a standing refusal.** p2 s14: “Any proposal should be ignored because accepting gives at most 34.” |
| 4 | 4-2 | p1 weak | 12 / 22 | 0 / −22 | **Ignored under a standing refusal.** p2 s15: “we want no deal. Need to wait for round 4 to close without accepting any proposal.” |
| 5 | 5-1 | p1 weak | 14 / 13 | +2 / −31 | **Ignored under a standing refusal.** p2 s18: “Do not accept any proposal unless it gives at least 44, which is impossible from a 27-point pot.” |
| 6 | 6-1 | p1 weak | 13 / 8 | +1 / −36 | **Ignored under a standing refusal.** p2 s20: “we will not accept any proposal because the total pot is too small to reach our outside option.” |

## Deals and failures

| Instance | Diagnosis |
|---:|---|
| 0 | **p2 strong proposed 23 / 44; p1 weak accepted in round 1.** p2 s5: “We can claim 44 and propose a split giving us 44 and p1 23.” p1 waited for a better counter, then accepted 23 with about 7 s left at its last state read. |
| 1 | **Timing cost a viable deal.** The strong seat did propose at its floor in round 1, but its claim closed the round before the weak seat could read either viable offer. The weak seat tried to accept +2 in round 2, too late. |
| 2 | **The viable offers crossed.** The weak seat did not read 25 / 80 or 28 / 78 live. Its 31 / 75 counter and fallback claim closed round 2. The round-3 pot could not pay both outside options. |
| 3 | **p2 strong proposed 38 / 58; p1 weak accepted in round 1.** p2 s5: “This is the minimal improvement over our outside option while leaving p1 as much as possible.” p1 first countered at 44 / 52, then took 38 instead of risking shrinkage. |
| 4 | **A claim closed the only viable round.** p2's 44 claim closed round 1 before its 20 / 48 and 24 / 44 counters could post. Either would have paid both outside options. Round 2 was already 2 points short of paying both floors. |

The event log records **31 posted proposals** and **2 successful acceptances**. Of 7 proposals that paid both outside options, the 2 that succeeded were in the 2 deal trials. In the failures, 4 viable offers expired without a live read by the recipient, and 1 received a late acceptance attempt. The audit excludes 6 unsuccessful proposal submissions; the table includes every proposal that posted. Some models described the 2 accepted splits as claim deals after the fact. Both world logs mark `via: proposal`, and both event streams record an `accept` by p1.

## Did proposals disclose outside options?

**No proposal explicitly stated an outside option.** A proposal could show what the proposer was willing to take, but it did not label the proposed amount as its minimum. Three strong seats posted one proposal at their exact outside option early: instance 0 p2 asked for 44, instance 1 p1 asked for 96, and instance 2 p2 asked for 78. The instance 3 strong seat asked for 58 against a 57-point outside option. Instance 4 p2's floor offer at 44 did not post in round 1, but its round-2 floor offer did. The instance 4 weak seat offered its exact 12-point floor in round 4, after the pot was already too small to pay the strong seat.

- **Some seats considered signaling.** Instance 2 p2 s5 considered an exact-floor proposal: “This signals minimum 78 and asks for more.” It did not post that contemplated round-1 signal. It later posted 28 / 78 and explained in s10: “Since our outside option is 78, we can also offer p1 up to 28 while keeping 78”. Instance 3 p2 s5 considered “a claim of 57 or proposal demanding57,” then actually asked for 58.
- **Recipients did not identify exact outside options from the posted splits.** Instance 3 p1 s9 treated 58 as “a 58-point demand for itself,” not the true 57. Instance 4 p1 s12 guessed 44 or 45 from the other seat's **claims**, not its unread proposals. Instance 1 p2 s7 likewise considered whether the 96 **claim** signaled the outside option. The world logs distinguish these claims from proposals.
