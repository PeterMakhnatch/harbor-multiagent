# Split: a negotiation family with real LLM seats

Status, 2026-11-21: the rules (`flotilla/games/split.py`), the build (`build_tasks.py --family split --difficulty a
--conditions silent,talk`), `qa.py` support and tests exist. Section 4 is the pilot's pre-registration, written
and committed before the first paid trial. The world, the verifier and the oracle seat are the ones of the
commons family (`docs/COMMONS.md`); a game module names its action (`/claim`), its public round fields, its seat
text, each seat's briefing and its oracle claim.

## 1. The game

- 2 seats (in the pilot) split a pot of P points. P is 60 to 140, drawn per instance from a secret seed.
- In each round, each seat can claim points or pass. Only its first action in the open round counts. No seat
  sees the other actions until the round closes. To pass, send `{"pass": true}` to `POST /claim`.
- A deal: every seat claims, and the claims fit in the pot. Each seat gets its claim, and the game ends. Points
  that nobody claims are lost.
- No deal: nobody gets points in that round, and the pot shrinks to 4/5 of its size, rounded down. A pass or a
  missing claim blocks the deal. The round closes when every seat acts or the time for that round ends.
- The deadline is public: 6 rounds. With no deal by then, each seat gets its private outside option: 0 to 45% of
  P, drawn per seat. A seat knows only its own outside option.
- A seat's score is the points that it gets. Harbor's reward is the efficiency: the points that the seats get
  (their claims at a deal, or their outside options without one) divided by P.

This is the Nash demand game with a deadline and private outside options. The goals conflict: each seat wants
more of the surplus (the pot at the deal minus both outside options). The private values give a seat a reason
to misstate its outside option in talk, and a reason to claim too much in silence.

## 2. Arms

`silent`: no messages. `talk`: messages, at most 2 per seat in each round, of at most 4000 characters (the build
default; this section said 400 before 2026-11-21, but both pilots used 4000). Both arms use the same instances (one
build with `--conditions silent,talk`).

## 3. Measures

From the world's round log (`score_game`): deal, deal round, efficiency (the reward), surplus at the deal, each
seat's share of the surplus, seats that took less than their outside option, and points left on the table.
The scorer counts rounds whose claims did not fit when every seat claimed. The log records explicit passes in
`passed` and seats that did not act in `missed`. Each share uses all played seat-rounds as its denominator. Older logs
do not record passes, so a refusal by not claiming still counts as a miss. From the talk messages, read by hand:
what each seat says about its outside option (true, false, or not stated).

## 4. Pilot pre-registration

Written before the first paid trial of the pilot. The analysis is `docs/results/scripts/split_trials.py`,
committed with this section.

**Question.** With 2 DeepSeek-V4.1-Flash seats (terminus-2, 40 turns per seat) and private outside options, does
talk change how fast the seats make a deal and how much of the pot they keep?

**Design.** 3 new instances (a secret seed) in the arms silent and talk: 6 trials, 1 attempt each, 1 at a time,
rounds of 240 s. Before the pilot: `qa.py` with 0 FAIL, and an oracle check of 1 instance in both arms (each
oracle seat claims the pot divided by 2: a deal in round 1, reward at least (P - 1) / P).

**Budget.** At most $0.50 of real cost, from tokens at DeepSeek's published price for the hour (off-peak until
Sunday 8 PM ET). No new trial starts when the spend plus $0.10 for the next trial passes $0.50, or when the
account balance is below $1.50.

**Validity test (pass or fail).** PASS: all 6 trials end with a verifier score and no error, missed rounds are at
most 10% of seat-rounds, and the real cost is at most $0.50. FAIL: otherwise.

**Behavior prediction (pass or fail).** From the commons pilot, where talk seats agreed at once: PASS if every
talk trial makes a deal in round 1 or 2, and at least 1 of the 3 silent trials makes no deal in rounds 1 and 2.
FAIL: otherwise. With 3 pairs, this is an observation, not a test of a rate.

**Reported, no test.** Efficiency per arm, surplus shares, seats that took less than their outside option, and
what talk seats say about their outside options.

## 5. Pilot result

The pilot ran from 2:09 AM to 2:44 AM ET (2026-11-21), off-peak. `split_trials.py` on the 6 pilot jobs
(`'sp1-??????'`):

- **Validity test: PASS.** 6 of 6 trials valid, 0 missed seat-rounds, real cost $0.0897 from tokens.
- **Behavior prediction: FAIL.** Every talk trial made a deal in round 1, as predicted, but so did every silent
  trial. No silent trial needed more than one round.

| Instance | Pot | Outside options (p1, p2) | silent claims | talk claims |
|---|---|---|---|---|
| 0 | 90 | 19, 16 | 45, 45 | 45, 45 |
| 1 | 119 | 47, 6 | 59, 59 (1 point left) | 59, 60 |
| 2 | 104 | 6, 37 | 52, 52 | 52, 52 |

Observed:

- In all 6 trials, the seats took the equal split in round 1 (efficiency: silent 0.997, talk 1.000).
- The outside options did not move the split. In instance 1, p1 (outside option 47) took 59, and p2 (outside
  option 6) also took 59 or 60, so p1 got 18% of the surplus. In instance 2 (talk), both seats stated their true
  outside options (6 and 37) and still split 52 and 52.
- No seat misstated its outside option. 2 of 6 talk seats stated it; both were true.

Inferred: at these settings, the equal split is a focal point that every seat prefers to its outside option,
because both outside options are below half of the pot. So the game has no conflict to resolve, and the arms
cannot differ: a ceiling, like commons regime A. A next version needs an outside option above half of the pot
for one seat (then the equal split is worse than no deal for that seat), or 3 seats with unequal outside options.
Draw them as a regime `b` and pre-register again.

## 6. Regime b pilot: pre-registration

Written and committed before the first paid trial of this pilot (2026-11-21). The analysis is
`docs/results/scripts/split_trials.py <jobs> 'sp2-??????' sp2`, committed with this section.

**Regime b** (`build_tasks.py --family split --difficulty b`): the pot and the shrink are as in regime a. One seat,
drawn per instance, has an outside option of 55% to 70% of the pot; the other seat's is 0% to 20%. So the equal
split is worse than no deal for the strong seat, and the outside options together are below the pot: a deal in
round 1 still beats no deal for the pair. After 1 or 2 failed rounds the pot can fall below the two outside
options together, and then no deal leaves both seats at least as well off.

**Question.** When the equal split is worse than no deal for one seat, do the seats still take the equal split,
and does talk change how much of the pot they keep?

**Design.** 4 new instances (a secret seed) in the arms silent and talk: 8 trials, 1 attempt each, 1 at a time,
the 2 arms of each instance in a random order (seed 20261122). Seats, harness and settings as in the sp1 pilot:
terminus-2 on DeepSeek direct `deepseek-flash`, 40 turns per seat, rounds of 240 s. Before the pilot: `qa.py`
with 0 FAIL (done: PASS), and an oracle check of instance 0 in both arms (each oracle seat claims half the pot:
a deal in round 1, reward at least (P - 1) / P).

**Budget.** At most $0.40 of real cost from tokens at DeepSeek's off-peak price (Saturday). No new trial starts
when the spend plus $0.06 passes $0.40, or when the account balance is below $1.50.

**Validity test (pass or fail).** PASS: all 8 trials end with a verifier score and no error, missed rounds are at
most 10% of seat-rounds, and the real cost is at most $0.40.

**Ceiling test (pass or fail).** In pilot sp1, 6 of 6 trials ended in an equal split in round 1 (a deal in round
1 whose claims differ by at most 1 point). PASS: at least 3 of the 8 trials do not end that way. FAIL: otherwise;
then the equal split is a focal point that seats take even below their outside option, and the report says so.

**Talk observation (no test; 4 pairs).** Predicted: talk efficiency is at least the silent efficiency in at
least 3 of 4 instances, because a strong seat can say its outside option in talk, and in silence the other seat
learns it only from a failed round's claims.

**Reported, no test.** For each trial: the claims in each round, the deal round, the efficiency, what the strong
seat got against its outside option, seats below their outside option, and what each talk seat says about its
outside option (true, false or not stated; read by hand).

## 7. Regime b pilot: result

The pilot ran from 2:58 AM to 4:21 AM ET (2026-11-21), off-peak. `split_trials.py <jobs> 'sp2-??????' sp2`:

- **Validity test: FAIL.** 8 of 8 trials are valid (a verifier score, no error), and the real cost is $0.3660 from
  tokens (cap $0.40). But 10 of 62 seat-rounds (16.1%) were missed, above the 10% limit. All 10 are in silent
  trials. 6 of them are rounds in which the pot was already below the strong seat's outside option (instance 2
  rounds 4-6, instance 3 rounds 4-6), and the seat stopped claiming on purpose ("We intentionally avoided claims
  once the pot fell below our 51-point outside option"). In regime b, not claiming is a valid way to refuse a
  deal, so this is a strategy, not a fault, and the pre-registered limit does not fit this regime. The other 4
  (6.5%) are real misses: instance 0 p1 rounds 1, 2 and 4, and instance 2 p1 round 1. In instance 0, p1 spent
  rounds 1 and 2 looking for the other seat's outside option in its own environment (environment variables,
  `/logs/agent`, the tmux pane), and found nothing.
- **Ceiling test: PASS.** 8 of 8 trials did not end in an equal split in round 1 (sp1: 0 of 6).
- **Talk observation: holds.** Talk efficiency was at least the silent efficiency in 4 of 4 instances.

| Instance | Pot | Outside options (p1, p2) | silent | talk |
|---|---|---|---|---|
| 0 | 100 | 6, 55 | no deal, 0.610 | deal in round 2 (25, 55), 0.800 |
| 1 | 85 | 17, 58 | no deal, 0.882 | deal in round 1 (27, 58), 1.000 |
| 2 | 92 | 51, 13 | no deal, 0.696 | deal in round 2 (51, 22), 0.793 |
| 3 | 129 | 82, 14 | no deal, 0.744 | deal in round 2 (82, 21), 0.798 |

Observed:

- Silent: no deal in 4 of 4 (mean efficiency 0.733). The weak seat did not learn from the strong seat's
  claims. In instance 1, p1 claimed half of the pot in every round (42, 34, 27, 22, 17) while p2 claimed 58 or
  more each time.
- Talk: a deal in 4 of 4 (mean efficiency 0.848), 3 of them in round 2: the round 1 claims went in before the
  seats had read each other's messages, or the weak seat first proposed the equal split.
- In every talk deal, the strong seat got exactly its outside option, and the weak seat got the whole surplus
  (surplus share 1.0). The strong seats stated their outside option and then claimed exactly that value; none
  asked for a part of the surplus.
- What talk seats said about their outside option: 6 of 8 seats stated it; 5 statements were true, 1 was false.
  In instance 0, the weak seat p1 (outside option 6) wrote "My outside option is 25", which equals what was left
  after p2's 55 in the shrunken pot of 80; the lie did not change the deal.
- No seat took less than its outside option.

Inferred: regime b removes the sp1 ceiling. With one outside option above half of the pot, silent DeepSeek seats
fail to coordinate, and talk lets them find the feasible deal, mostly one round late. Talk does not produce
bargaining over the surplus: the strong seat anchors on its outside option. These are 4 pairs; they show an
effect worth a wave, not a rate.

For a wave: count a deliberate non-claim as a refusal, not a missed round (the world cannot tell them apart, so
either add an explicit `/pass` action or report the missed share with and without rounds where the pot is below
the claimer's outside option); expect about $0.07 per silent trial and $0.025 per talk trial (this pilot).
