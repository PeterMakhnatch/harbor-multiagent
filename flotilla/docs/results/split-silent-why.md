# Why the silent Split trials reached no deal

**Result: 0 of 10 finished silent trials reached a deal.** In 7 trials the weak seat opened at half, and the strong seat claimed more than half. In the other 3 trials, one seat missed round 1: 2 searched for information and 1 waited to see the other claim. The round-2 pot could still pay both outside options in 8 trials, but the seats did not make compatible claims. From round 4 onward, no pot could pay both outside options in any trial.

**Coverage:** All 4 silent sp2 trials and all 6 silent sp3 trials. Included sp3 jobs, in instance order: `sp3-1c5aa5`, `sp3-f5cbd4`, `sp3-c111be`, `sp3-aa960b`, `sp3-2c8d99`, and `sp3-a799be`. All 10 results were valid. Talk trials are excluded.

## Decisions by seat and round

Each cell gives the class, the logged action, the trajectory `step_id`, and a verbatim excerpt from the model. A number is a booked claim in points. `miss` means no booked action; `pass` means an explicit pass. A quote can support multiple rounds when one decision controls later actions. The world log determines which round a claim landed in.

- **A:** Equal split anchor. **B:** Uses public claims or passes to estimate the other seat's outside option or effective minimum demand, and sets or keeps its claim on that basis. An estimate can be wrong. Not every concession is B.
- **C:** Strong seat sets or enforces its outside-option floor. This includes claims above the floor when the floor is the stated reason, deliberate non-claims, passes, and claims above the pot to block a deal.
- **D:** Chooses a claim to send a signal. **E:** Spends the round searching for hidden information instead of choosing. **F:** Timing prevents the intended claim from counting. **G:** Other, including a weak seat's own fallback floor and deliberate observation of a closed round. The table records the main reason, not every reason the seat considered.

| Trial | Seat | Round 1 | Round 2 | Round 3 | Round 4 | Round 5 | Round 6 |
|---|---|---|---|---|---|---|---|
| sp2-350d29 | p1 weak | E · miss · s7 “inspect environment/config variables and local logs for any hints about the hidden opponent type or game setup” | F · miss · s10→11 “Our claim of 18 was submitted just after round 2 closed” | B · 18 · s10 “p2 may be demanding roughly 78% of the current stock” | F · miss · s14→15 “the script wisely did not submit a late claim” | D · 18 · s16 “Good signal repeated demand 18.” | B · 9 · s18 “a claim of 9 leaves room for a deal up to opponent claim 23” |
| sp2-350d29 | p2 strong | C · 78 · s7 “claims at or below 55 are dominated by the fallback” | C · 67 · s9 “any accepted claim must exceed 55” | C · 56 · s13 “the minimal claim above the fallback” | C · 100 · s14 “force no deal for the remaining rounds and take the 55 fallback” | C · 100 · s18 “stock 40, below our 55-point outside option” | C · 100 · s19 “submit a blocking claim of 100” |
| sp2-91f58b | p1 weak | A · 42 · s6 “A reasonable opening claim is 42, near half.” | A · 34 · s7 “A claim of 34 is half the current pot” | A · 27 · s13 “A claim of 27 is exactly half the current pot” | A · 22 · s14 “Since a fair split should now be feasible” | G · 17 · s15 “claiming 17 preserves at least the outside option” | G · 17 · s16 “claiming 17 guarantees at least the fallback value” |
| sp2-91f58b | p2 strong | C · 58 · s3 “Claiming 58 is the natural safe anchor” | C · 60 · s7 “any future deal below 58 is worse than no deal” | C · 55 · s8 “force failures in all remaining rounds by claiming more than the pot” | C · 44 · s12 “no possible deal from now on is preferable to no deal” | C · 35 · s13 “continue forcing failed rounds so the game ends with our 58-point outside option” | C · 28 · s18 “guarantee no deal in round 6 by claiming more than the pot” |
| sp2-0ce625 | p1 strong | G · miss · s5 “wait for round 1 to close without claiming” | C · 51 · s6 “bidding below 51 could lock in less than our fallback” | C · 52 · s8 “avoids accidentally accepting a below-outside deal” | C · miss · s9 “intentionally avoid claiming in rounds 4, 5, and 6” | C · miss · s14 “every remaining pot is below our outside option of 51” | C · miss · s17 “any possible deal in this final round would score worse” |
| sp2-0ce625 | p2 weak | A · 46 · s4 “A natural focal allocation is an even split” | A · 36 · s10 “A fair split of the remaining pot is 36 or 37 points.” | A · 29 · s11 “A fair split now would be 29 points each” | A · 23 · s15 “The equitable split is 23 each.” | G · 36 · s18 “If p1 misses again, claiming the full pot lets us settle” | A · 14 · s22 “a natural fair split of the remaining pot” |
| sp2-ebdc9b | p1 strong | C · 106 · s3 “any claim below 82 is dominated” | C · 103 · s4 “the round overclaims and the pot shrinks to 82, preserving our outside option of 82” | C · 82 · s9 “The current pot equals our outside option of 82” | C · miss · s12 “We should not claim in any remaining round” | C · miss · s16 “continue not claiming in rounds 5 and 6” | C · miss · s21 “the pot remains below our 82-point outside option” |
| sp2-ebdc9b | p2 weak | A · 64 · s4 “a reasonable focal split” | B · 18 · s10 “p1's first claim was about 82% of the pot” | G · 18 · s11 “our round 2 claim of 18 was more cooperative, so p1 may moderate its demand” | G · 15 · s12 “if p1 ever leaves at least 15, we improve on the outside option” | G · 15 · s18 “keeping the claim low maximizes the chance of a deal” | G · 15 · s25 “a safe final-round choice with upside” |
| sp3-1c5aa5 | p1 weak | A · 33 · s4 “if the other player claims a similar half” | G · 20 · s7 “a strategic concession that still yields more than the outside option” | B · 10 · s8 “p2's first two claims were 55 and 44, both equal to floor(5/6 * stock)” | G · 10 · s10 “if it makes a greedy but deal-oriented claim such as 23” | B · 10 · s11 “So outside likely≥23. No claim beating9 will be accepted.” | G · 10 · s12 “A higher claim such as 11 risks p2 claiming 10 and blocking the deal.” |
| sp3-1c5aa5 | p2 strong | C · 55 · s3 “any claim at or above 44 is safe versus the fallback” | C · 44 · s4 “Claiming 44 ensures no downside” | C · pass · s10 “Passing explicitly blocks a deal in each remaining round” | C · pass · s14 “below our outside option of 44, so passing remains strictly best” | C · pass · s20 “no possible deal can beat the guaranteed 44-point fallback” | C · pass · s21 “any deal is worse than our 44-point outside option” |
| sp3-f5cbd4 | p1 strong | C · 118 · s4 “a first-round claim above 96 can only improve our score when p2 accepts” | C · 97 · s5 “minimum amount that beats our outside option” | C · pass · s12 “our optimal move is to pass every remaining round” | C · pass · s12→16 “continuing to pass guarantees our fallback score” | C · pass · s12→18 “Our background script has already passed round 5” | C · pass · s12→19 “Passing guarantees no deal and our 96-point outside option.” |
| sp3-f5cbd4 | p2 weak | A · 70 · s5 “A claim of 70 is a natural fair split” | G · 35 · s8 “if p1 moderates to 77 or below” | B · 14 · s9 “p1's claims reveal high reservation/demand.” | G · 14 · s10 “A claim of 14 maximizes our chance of a profitable deal” | G · 14 · s11 “Claiming 14 remains the smallest claim that improves on our outside option” | G · 14 · s12 “Claiming 14 still gives us the best chance of a profitable deal” |
| sp3-c111be | p1 weak | A · 66 · s4 “A claim of 66 is a robust fair split” | A · 53 · s13 “A fair and likely acceptable claim is 53, exactly half the current pot.” | A · 42 · s17 “A half-pot claim of 42 is a natural fair target” | G · 19 · s19 “a claim of 19 is a safe floor this round” | G · 34 · s20 “if p2's outside option is no higher than ours” | A · 21 · s21 “A focal split of 21 matches half the remaining pot” |
| sp3-c111be | p2 strong | E · miss · s7 “inspecting the agent logs may reveal whether other player activity or prior sessions are recorded” | C · 105 · s8 “any claim above 78 has upside with no downside to our score if no deal occurs” | C · 84 · s9 “any failed deal still falls back to our 78 outside option” | C · pass · s10 “Any accepted deal from now on would score us below 78” | C · pass · s14 “pass again to block any deal, preserving the 78-point fallback” | C · pass · s17 “we must pass to force the outside-option fallback” |
| sp3-aa960b | p1 weak | A · 48 · s3 “An equal split claim of 48 is a natural focal point” | A · 38 · s10 “The focal equal split of the current pot is 38” | A · 30 · s11 “A focal equal split of the current pot is 30” | A · 24 · s12 “a fair claim of half the current pot gives us 24” | B · 13 · s13 “This suggests p2's outside option is at least around 25.” | G · 7 · s14 “in the final round we should claim 7: this guarantees at least our outside option” |
| sp3-aa960b | p2 strong | C · 57 · s7 “Therefore our claim should be at least 57.” | C · 57 · s8 “claiming exactly 57 guarantees a floor of 57” | C · 60 · s11 “claiming the full current stock of 60 is a free upside move” | C · pass · s12 “Any deal now would give us less than 57” | C · pass · s13 “pass to prevent any deal from paying us less than 57” | C · pass · s16 “passing guarantees we receive the superior outside option of 57” |
| sp3-2c8d99 | p1 weak | A · 34 · s3 “A fair split is 34 points each” | A · 27 · s14 “a fair split is 27 each” | A · 21 · s15 “A claim near half the current pot (21)” | G · 12 · s16 “I should avoid accepting less than my outside option of 12.” | G · 12 · s17 “claim exactly 12” | G · 12 · s21 “Claiming 12 guarantees our outside option value” |
| sp3-2c8d99 | p2 strong | C · 56 · s8 “Since any deal below 44 is worse than our fallback, our first-round claim should be at least 44.” | C · 44 · s9 “A claim of 44 is the weakest claim that still matches our outside option” | C · pass · s13 “We should pass for all remaining rounds to block any deal and secure 44 points.” | C · pass · s15 “continue passing to prevent any deal below our fallback” | C · pass · s17 “far below our 44-point outside option, so passing remains strictly optimal” | C · pass · s18 “guarantee our outside option of 44 points” |
| sp3-a799be | p1 weak | A · 48 · s3 “Common target: claim half (48 or49).” | A · 38 · s11 “Claim a fair half of the current pot, 38 points.” | B · 19 · s13 “P2's observed claims have dropped from 76 to 57, suggesting its next ask may be around 42.” | G · 15 · s16 “Claiming 15 maximizes the chance of a deal at a score equal to our outside option” | G · 23 · s18 “If p2's outside likely similar (maybe random around15), this is a natural focal point.” | G · 15 · s20 “Our outside option is 15, so claiming 15 guarantees us at least 15” |
| sp3-a799be | p2 strong | C · 76 · s5 “Claiming 76 gives a chance to earn more if p1's claim is 21 or less, while preserving fallback if not.” | C · 57 · s6 “A claim of 57 has no downside versus 55” | C · 55 · s9 “Claiming 55 guarantees at least 55” | C · pass · s10 “Any deal now would give us at most 48, worse than the guaranteed fallback.” | C · pass · s17 “Claiming any positive amount would be worse than passing and eventually receiving 55” | C · pass · s19 “Passing blocks a deal and guarantees the 55-point outside option” |

`sp2-350d29` p1's round-3 claim carried over from round 2. The 78% estimate used the wrong pot for the round in which the claim landed. Its round-6 estimate of 23 extended the first 3 claims even after 2 intervening claims of 100. `sp2-91f58b` p1's round-5 claim was both half and its own fallback; the decision states the fallback as its reason. `sp3-f5cbd4` p2 suspected a high outside option in rounds 4–6 but repeated its minimum profitable claim without a further adjustment. `sp2-0ce625` p2's round-5 reasoning assumed a miss could pay the full pot; a miss actually blocks any deal.

## Counts

| Class | Strong | Weak | Total |
|---|---:|---:|---:|
| A — equal split | 0 | 25 | 25 |
| B — inferred minimum | 0 | 8 | 8 |
| C — strong seat's outside option | 58 | 0 | 58 |
| D — intended claim signal | 0 | 1 | 1 |
| E — search instead of claim | 1 | 1 | 2 |
| F — timing | 0 | 2 | 2 |
| G — other | 1 | 23 | 24 |
| **Total** | **60** | **60** | **120** |

The sp2 world logged 10 misses: 6 deliberate refusals below the strong seat's outside option, 1 deliberate observation round, 1 search, and 2 timing misses. The sp3 world logged 21 explicit passes and 1 search-related miss. In `sp2-350d29` p1 round 2, the claim landed in round 3. In round 4, the script found 0.9 s left and skipped the claim because it required more than 3 s. In `sp3-c111be`, p2 searched past the round-1 deadline. Its first claim used the stale round-1 pot and landed in round 2.

## Strong-seat claims above the outside option

The table subtracts the strong seat's outside option from the claim logged in each round, in points. A miss is not a zero-point claim.

| Trial | Strong seat | Outside option | Round 1 | Round 2 |
|---|---|---:|---:|---:|
| sp2-350d29 | p2 | 55 | +23 | +12 |
| sp2-91f58b | p2 | 58 | 0 | +2 |
| sp2-0ce625 | p1 | 51 | miss | 0 |
| sp2-ebdc9b | p1 | 82 | +24 | +21 |
| sp3-1c5aa5 | p2 | 44 | +11 | 0 |
| sp3-f5cbd4 | p1 | 96 | +22 | +1 |
| sp3-c111be | p2 | 78 | miss | +27 |
| sp3-aa960b | p2 | 57 | 0 | 0 |
| sp3-2c8d99 | p2 | 44 | +12 | 0 |
| sp3-a799be | p2 | 55 | +21 | +2 |

The strong seat claimed above its outside option in 12 of 18 recorded claims: 6 of 8 in round 1 and 6 of 10 in round 2. In `sp2-ebdc9b` and `sp3-c111be`, the round-2 pot could pay both outside options, but the strong claim left less than the weak seat's outside option. Only in `sp2-ebdc9b` would reducing the strong claim to its outside option have fit the weak seat's actual claim: 82 + 18 ≤ 103. Claims above the floor did not explain every failure. In `sp3-aa960b`, the strong seat claimed its exact outside option in both rounds, and neither split fit.

## Trial diagnoses

| Trial | Outside options (p1, p2) | Why no deal happened |
|---|---|---|
| sp2-350d29 (instance 0) | 6, 55 | p1 did not claim in the first 2 rounds. In round 3, 18 + 56 exceeded 64. p2 then blocked every deal below its 55-point fallback. |
| sp2-91f58b (instance 1) | 17, 58 | p1 opened at 42 when p2's floor of 58 left at most 27. Round 2's pot of 68 could not cover both fallbacks. |
| sp2-0ce625 (instance 2) | 51, 13 | p1 skipped round 1 to learn p2's claim and incorrectly expected no shrink. p2 kept claiming half in rounds 2–3, then p1 stopped claiming. |
| sp2-ebdc9b (instance 3) | 82, 14 | p2 cut from 64 to 18 in round 2. p1 claimed the entire 103-point pot, then the full 82 in round 3 and stopped claiming. |
| sp3-1c5aa5 (instance 0) | 9, 44 | Round 2 could fit only the fallback split of 9 + 44; claims were 20 + 44. p2 passed after the pot fell below 44. |
| sp3-f5cbd4 (instance 1) | 96, 13 | p2 cut from 70 to 35, but p1's 97 left only 15. p1 passed from round 3 when the pot fell below 96. |
| sp3-c111be (instance 2) | 19, 78 | p2 searched past the round-1 deadline. Its intended 105 landed against 53 in round 2's pot of 106. It claimed the full 84 in round 3, then passed. |
| sp3-aa960b (instance 3) | 7, 57 | p2 claimed 57 in both early rounds. p1 claimed half even after seeing 57. No later pot could pay both outside options. |
| sp3-2c8d99 (instance 4) | 12, 44 | p1's 34 and p2's 56 exceeded 68. The round-2 pot of 54 was below the combined outside options, 56. p2 passed from round 3. |
| sp3-a799be (instance 5) | 15, 55 | p1 claimed half twice while p2 asked above 55. In round 3, p1 cut to 19, but the pot of 61 could not pay both outside options. |

## Searches for the other seat's private value

**No seat found the other seat's private value in files, environment variables, processes, logs, or an extra endpoint.** The claims and passes in the public round log were separate evidence.

| Seat | What it inspected | What it found |
|---|---|---|
| sp2-350d29 p1 | s4–8: service documentation, local directories, processes, environment, its agent logs, and its tmux pane. | Its own seat data and terminal capture, not the other seat's value. It lost round 1 during the search; its round-2 claim then landed in round 3. |
| sp2-350d29 p2 | s4–6: filtered environment, local listing, and service documentation in a broader search for metadata. | World and own-seat variables, a local listing, and no useful API metadata. It still claimed in round 1. |
| sp2-0ce625 p1 | s3–4 and s7: service documentation, home directory, processes, local config, and state variants for extra player information. | No other-seat data. The extra state queries returned the same seat view, and the documentation routes did not exist. The seat chose to skip round 1 after the first search and still claimed in round 3 after the second. |
| sp3-1c5aa5 p1 | s6 and s9: home files and environment for hidden context, then service documentation. | Its own environment and briefing; no other-seat value or useful documentation. It still claimed in rounds 2 and 4. |
| sp3-f5cbd4 p1 | s3: environment, home files, and service root before choosing a claim; the model considered whether these could expose p2's value. | Its own briefing and no p2 value. It still claimed in round 1. |
| sp3-f5cbd4 p2 | s7: environment and processes explicitly to learn p1's outside option or behavior. | No useful information about p1. It still claimed in round 2. |
| sp3-c111be p2 | s3–7: environment, service documentation, local files, processes, agent logs, and terminal recording. It explicitly looked for p1's hidden value. | Its own terminal recording, with no evidence of p1's session or value. It did not submit a claim before round 1 closed. |
| sp3-aa960b p2 | s4–6: filtered environment, service documentation, local directories, and a search for game files. It explicitly checked whether outside-option details were exposed. | Its own briefing and no p1 value or useful documentation. It still claimed in round 1. |
| sp3-2c8d99 p2 | s5–7: environment, service schema, local files, processes, and host configuration for extra game information. | Its own role and 44-point outside option, plus the game setup. It found no p1 value and still claimed in round 1. |

Routine state queries, checks for the seat's own world URL or token, and API documentation checks with no stated search for opponent or game data do not count as private-value searches.
