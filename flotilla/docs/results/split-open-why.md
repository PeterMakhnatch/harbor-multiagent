# Why the open Split trials reached no deal

**None of the 6 open trials reached a deal.** Both seats knew both outside options and could not send messages. In all 5 recorded strong-seat opening claims, the strong seat asked for surplus and expected the weak seat to accept its outside option or a small amount above it. The weak seats did not choose those amounts. There were 3 round-1 misses, but the 3 intended claims arrived in round 2. All 3 would also have exceeded the opening pot with the other seat's actual opening claim.

**Coverage:** All 6 `sp4` open trials and their 6 matched `sp3` silent trials. Job IDs appear in the comparison table. The runs used the same instances but were not randomized in time. Each arm had 0 deals in these 6 trials.

## Decision classes

- **A:** Anchors on half of the pot. An equal split of the surplus after subtracting outside options is not half of the pot; it is G unless another class applies.
- **B:** Uses the other seat's public claims or passes to infer its effective demand and choose a claim. Using a value disclosed in the briefing alone is not B.
- **C:** The strong seat sets or enforces its own outside-option floor. This includes a pass, a claim above the pot, or a speculative claim whose main reason is to protect the floor.
- **D:** Chooses a claim size to send a signal. **E:** Searches for hidden game or opponent information instead of choosing in time. **F:** Timing prevents the intended claim from counting, without an E search as the main observed delay.
- **G:** Other. This includes a weak seat claiming what remains after the strong seat's known outside option and a weak seat passing when a deal cannot pay both options.
- **H:** The strong seat claims above its own outside option because it expects the weak seat to accept its known outside option or a small amount above it. Hoping for a zero claim below the known weak outside option does not count.
- **I:** The weak seat claims its own outside option or stays within 2 points above it to leave room for a deal. Use I for an exact-floor claim justified as safe, even if the seat also cites public claims. Use B when a specific prediction sets a claim above the floor. A pass is not a concession claim.

## Decisions by seat and round

Each cell gives the main class, action recorded in the world log, `step_id`, and a verbatim excerpt. A number is a claim in points; `miss` means no recorded action and `pass` means an explicit pass. An arrow links a decision to its confirmation, or a late claim to the step that identifies the round in which it landed.

| Trial | Seat | Round 1 | Round 2 | Round 3 | Round 4 | Round 5 | Round 6 |
|---|---|---|---|---|---|---|---|
| sp4-52501d | p1 weak | G · 23 · s3 “The maximum we can safely claim in round 1 while leaving p2 at least 44 is 67 - 44 = 23.” | I · 9 · s13 “Claiming 9 at least preserves the possibility of a deal with no downside versus our outside option.” | I · 9 · s14 “Claiming 9 is safe and preserves the possibility of a 9-point deal” | I · 9 · s15 “claim 9 to preserve any chance of a deal” | G · 17 · s16 “claiming 17 instead of 9 weakly dominates” | G · 20 · s17 “Claiming the maximum of 20 weakly dominates any lower claim” |
| sp4-52501d | p2 strong | F · miss · s5→6 “Our claim endpoint applied to round 2, so round 1 was missed” | G · 51 · s5 “An equal split of that surplus gives us 44 + 7 = 51 and p1 9 + 7 = 16” | C · pass · s6 “With only 42 points available and our outside option worth 44, no deal can match our outside option.” | C · pass · s9 “any deal is worse than the fallback, so we should pass again” | C · pass · s12 “pass again to block a deal and preserve the fallback” | C · pass · s13 “far below our outside option of 44” |
| sp4-7e64ce | p1 strong | H · 127 · s4 “Claiming 127 anchors high while still allowing p2 to match their outside option” | H · 98 · s8 “Claiming 98 leaves p2 up to 14, which is strictly better than p2's outside option of 13” | C · pass · s10 “accepting any deal from round 3 onward would make us worse off than the guaranteed outside option” | C · pass · s11→15 “block every remaining round with a pass and collect the 96 outside option” | C · pass · s11→15 “block every remaining round with a pass and collect the 96 outside option” | C · pass · s11→15 “block every remaining round with a pass and collect the 96 outside option” |
| sp4-7e64ce | p2 weak | E · miss · s3 “inspect the local environment for any additional task files or documentation that may clarify opponent behavior or available tooling” | G · 40 · s5 “A claim of 40 is a negotiation demand that could yield 40 if p1 demands 100 or less” | B · 15 · s6 “If this pattern continues, round 3 p1 will claim 74, leaving p2 15.” | I · 13 · s7 “Claiming 13 gives the same result as the outside option if a deal somehow occurs” | I · 13 · s8 “claiming 13 is safe if a deal somehow occurs” | I · 13 · s9 “Claiming 13 is safe and matches our outside option” |
| sp4-51baef | p1 weak | G · 55 · s4 “if p2 claims a reasonable amount up to 78, the total is at most 133 and p1 gets 55” | G · 28 · s8 “p1 can claim at most 28 while still allowing p2 to receive 78” | B · 20 · s9 “If p2 continues that pattern, it will claim about 64, making a p1 claim of 20 acceptable” | I · 20 · s10 “if p2 unexpectedly claims 47 or less, p1 can get a small improvement” | I · 20 · s11 “Our best realistic fallback is our outside option 19, but claiming 20 is weakly better” | I · 20 · s12 “Claiming 20 does not risk that fallback and could only help if p2 unexpectedly claims 22 or less.” |
| sp4-51baef | p2 strong | H · 112 · s5 “claiming 112 leaves 21 for p1, which is strictly better for p1 than the 20 they could secure in round 2” | H · 86 · s6 “Claiming 86 leaves p1 20, strictly better than their continuation, making a deal mutually preferable.” | C · 78 · s13 “claiming 78 should secure a deal at our outside-option value” | C · pass · s15 “the current pot is below our outside option of 78” | C · pass · s18 “pass again to guarantee our outside option at the end” | C · pass · s20 “passing is optimal and guarantees our 78-point outside option” |
| sp4-72be5f | p1 weak | G · 23 · s3 “A reasonable focal claim is the equal split of the surplus over the two outside options” | G · 19 · s5 “p2 needs at least 57, so p1 can claim at most 19” | I · 7 · s7 “claiming 7 is the safe play and may end the game now with score 7” | G · pass · s10 “Passing in remaining rounds safely blocks any deal” | G · pass · s15 “Passing is the safe action to block any deal and preserve our outside option of 7.” | G · pass · s19 “no mutually beneficial deal is possible” |
| sp4-72be5f | p2 strong | H · 89 · s4 “Claiming 89 maximizes upside if p1 claims 7” | H · 69 · s8 “The maximum claim that can still leave p1 their outside option of 7 is 76-7=69.” | C · 60 · s9 “it yields 60 if p1 somehow claims 0, while in all other cases the game will still end with our outside option 57 anyway” | C · pass · s10 “forcing no deal guarantees us 57” | C · pass · s11 “any deal now would leave us worse off than forcing no deal” | C · pass · s12 “Passing blocks any deal and guarantees our outside option” |
| sp4-ddf0a9 | p1 weak | G · 24 · s4 “Claiming 24 maximizes p1's payoff while keeping a deal possible if p2 claims 44.” | I · 12 · s6 “Our best fallback is the outside option of 12, so we should claim 12” | I · 12 · s7 “Our best move is to claim our outside option of 12” | G · 34 · s8 “claim the full pot in each remaining round in case p2 passes or claims zero” | G · 27 · s9 “claim the full pot in the remaining rounds” | G · 21 · s10 “claim the full pot in case p2 passes or claims zero” |
| sp4-ddf0a9 | p2 strong | H · 56 · s3 “Claiming 56 is safe: if p1 claims 12 or less, we get 56” | C · 44 · s4 “Claiming 44 guarantees us at least our outside option while blocking any unfavorable deal.” | C · 44 · s6 “Claiming 44 blocks any possible deal and preserves our guaranteed outside option” | C · 44 · s7 “force no deal through the remaining rounds and receive the outside option at the end” | C · 44 · s8 “block any deal again by claiming 44” | C · 44 · s9 “Claiming 44 guarantees that no deal can occur” |
| sp4-a6caf1 | p1 weak | E · miss · s6→8 “The first-round claim was too late” | G · 42 · s7→8 “claim the maximum amount compatible with p2 receiving its outside option: 97 - 55 = 42” | G · pass · s10 “pass to guarantee no deal and preserve our outside option” | G · pass · s11 “Passing blocks any deal and guarantees our outside option of 15” | G · pass · s15 “Passing blocks the deal and preserves our outside option of 15.” | G · pass · s16 “no deal can improve on p2's outside option” |
| sp4-a6caf1 | p2 strong | H · 80 · s3 “Claiming 80 leaves p1 17, which is strictly better for p1 than its continuation payoff” | H · 61 · s8 “Claiming 61 leaves p1 16, which is strictly better than its outside option of 15” | C · 55 · s9 “Our final fallback is 55, so the optimal safe action is to claim exactly 55” | C · pass · s11 “the maximum possible deal share is 48, which is less than our outside option of 55” | C · pass · s12 “This is below our outside option of 55, so any deal would worsen our score.” | C · pass · s14 “A deal would reduce our score, so we should pass to block any agreement” |

In `sp4-52501d`, p2 spent part of round 1 analyzing the game offline before submitting its intended 51. In `sp4-7e64ce`, p2 checked local files, environment variables, and service documentation before submitting 40. In `sp4-a6caf1`, p1 searched service endpoints and its environment before submitting 42. The searches did not yield useful opponent information. Both outside options were already in the briefing. The table uses the logged round for all 3 late claims. Some seats also reasoned that a pass could let a full-pot claim win. The rules say a pass blocks any deal.

## Counts by seat role

| Class | Strong | Weak | Total |
|---|---:|---:|---:|
| A — Half-pot anchor | 0 | 0 | 0 |
| B — Inferred demand from public actions | 0 | 2 | 2 |
| C — Enforced strong-seat floor | 25 | 0 | 25 |
| D — Signaled through claim size | 0 | 0 | 0 |
| E — Searched instead of deciding in time | 0 | 2 | 2 |
| F — Timing without a search | 1 | 0 | 1 |
| G — Other | 1 | 20 | 21 |
| H — Claimed surplus against known weak floor | 9 | 0 | 9 |
| I — Claimed at or near weak floor | 0 | 12 | 12 |
| **Total** | **36** | **36** | **72** |

## Trial diagnoses

| Trial | Instance | Why no deal happened |
|---|---:|---|
| sp4-52501d | 0 | p2's intended 51 missed round 1, where p1 had already asked 23. In round 2, 9 + 51 exceeded 53. p2 then passed. |
| sp4-7e64ce | 1 | p2's intended 40 missed round 1, where p1 had asked 127. In round 2, 98 + 40 exceeded 112. p1 then passed. |
| sp4-51baef | 2 | p1 left exactly 78 in both early rounds. p2 asked 112 and then 86. The round-3 pot could not pay both outside options. |
| sp4-72be5f | 3 | p1 first split surplus, then left exactly 57. p2 left only 7 for p1 both times. p1 took that cue in round 3, when the pot was already too small. |
| sp4-ddf0a9 | 4 | Both seats asked for all 12 points of opening surplus. In round 2 they each claimed their floor, but 12 + 44 already exceeded 54. |
| sp4-a6caf1 | 5 | p1's intended 42 missed round 1, where p2 asked 80. In round 2, 42 + 61 exceeded 77. From round 3, p1 passed. |

## What disclosure changed in round 1

Claims below are in points and ordered **weak / strong**. An arrow after a miss gives the intended round-1 amount that landed in round 2. It is not a recorded round-1 claim.

| Instance | Silent job | Open job | Silent weak / strong | Open weak / strong |
|---|---|---|---|---|
| 0 | sp3-1c5aa5 | sp4-52501d | 33 / 55 | 23 / miss → 51 |
| 1 | sp3-f5cbd4 | sp4-7e64ce | 70 / 118 | miss → 40 / 127 |
| 2 | sp3-c111be | sp4-51baef | 66 / miss | 55 / 112 |
| 3 | sp3-aa960b | sp4-72be5f | 48 / 57 | 23 / 89 |
| 4 | sp3-2c8d99 | sp4-ddf0a9 | 34 / 56 | 24 / 56 |
| 5 | sp3-a799be | sp4-a6caf1 | 48 / 76 | miss → 42 / 80 |

**Weak seats:** All 6 silent seats opened at half of the pot, rounded down. None of the 4 open weak seats with a recorded opening claim chose half. All 4 claimed less than their matched silent seat: 3 left exactly the strong seat's outside option, and 1 split the surplus equally. Both late weak claims were also below half; the intended 42 left exactly the strong seat's outside option at the opening pot.

**Strong seats:** All 5 recorded open claims were above their outside option, compared with 4 of 5 recorded silent claims. In the 4 instances where the strong seat opened in both arms, its open claim rose in 3 and stayed the same in 1. Each of the 5 recorded open claims left the weak seat 0–2 points above its outside option. The late 51 instead aimed to divide surplus equally.

**Interpretation:** Disclosure moved the weak seats away from half-pot openings, but the seats did not use the same split. Both seats wanted some or all of the same surplus. The same 0-of-6 outcome does not mean the opening strategies were the same. The matched comparison is descriptive, since the arms ran at different times.
