# Split: a negotiation family with real LLM seats

Status, 2026-11-21: the rules (`flotilla/games/split.py`), the build (`build_tasks.py --family split --difficulty a
--conditions silent,talk`), `qa.py` support and tests exist. Section 4 is the pilot's pre-registration, written
and committed before the first paid trial. The world, the verifier and the oracle seat are the ones of the
commons family (`docs/COMMONS.md`); a game module names its action (`/claim`), its public round fields, its seat
text, each seat's briefing and its oracle claim.

## 1. The game

- 2 seats (in the pilot) split a pot of P points. P is 60 to 140, drawn per instance from a secret seed.
- In each round, each seat claims a number of points, once. No seat sees the other claims until the round closes.
- A deal: every seat claims, and the claims fit in the pot. Each seat gets its claim, and the game ends. Points
  that nobody claims are lost.
- No deal: nobody gets points in that round, and the pot shrinks to 4/5 of its size, rounded down. A seat that
  does not claim blocks the deal of that round.
- The deadline is public: 6 rounds. With no deal by then, each seat gets its private outside option: 0 to 45% of
  P, drawn per seat. A seat knows only its own outside option.
- A seat's score is the points that it gets. Harbor's reward is the efficiency: the points that the seats get
  (their claims at a deal, or their outside options without one) divided by P.

This is the Nash demand game with a deadline and private outside options. The goals conflict: each seat wants
more of the surplus (the pot at the deal minus both outside options). The private values give a seat a reason
to misstate its outside option in talk, and a reason to claim too much in silence.

## 2. Arms

`silent`: no messages. `talk`: messages, at most 2 per seat in each round, of at most 400 characters. Both arms
use the same instances (one build with `--conditions silent,talk`).

## 3. Measures

From the world's round log (`score_game`): deal, deal round, efficiency (the reward), surplus at the deal, each
seat's share of the surplus, seats that took less than their outside option, points left on the table, rounds
whose claims did not fit, and missed rounds. From the talk messages, read by hand: what each seat says about its
outside option (true, false, or not stated).

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
