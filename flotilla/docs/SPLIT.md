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

`open`: no messages, as in `silent`, but every seat sees every outside option in its instruction and briefing.
Build all 3 arms with `--conditions silent,talk,open`. To add only `open`, use `--conditions open` and
`--seed-of <earlier-label-folder>`. Use the same difficulty, seat count and instance numbers as the earlier build.

`propose`: no messages, and outside options stay private. Each seat can post up to 2 splits per round with
`POST /propose`. Each split gives a whole number of points (0 or more) to each seat and cannot exceed the pot
at the time it is posted. At most 1 proposal per seat stays open: a newer proposal from that seat replaces the
older one. `/state` and `/wait` show the same open proposals: the ID, proposer, split, and round posted. When
all other seats accept a proposal, the game ends at that split. Any other seat can decline it with
`POST /decline {"id": "<id>"}`. A decline closes the proposal for every seat. In world v10, a pending
proposal prevents claims and passes from closing the round early, but the timer still closes it. An
unanswered proposal survives that close if its split fits the new pot. Otherwise it lapses. Open proposals
expire at game end. The score counts proposals by fate, feasible proposals (each seat gets at least its
outside option), and feasible proposals that no other seat saw or accepted. Seats do not see these counts.
The round log records `inactive`: seats that neither claim, pass, post, accept, nor decline in that round.

## 3. Measures

From the world's round log (`score_game`): deal, deal round, efficiency (the reward), surplus at the deal, each
seat's share of the surplus, seats that took less than their outside option, and points left on the table.
The scorer counts rounds whose claims did not fit when every seat claimed. The log records explicit passes in
`passed` and seats that did not act in `missed`. Each share uses all played seat-rounds as its denominator. Older logs
do not record passes, so a refusal by not claiming still counts as a miss. From the talk messages, read by hand:
what each seat says about its outside option (true, false, or not stated). `docs/results/scripts/speech_acts.py`
(exploratory, no test) does this without a reader: outside-option statements (true, inflated, deflated), text splits
("I claim 57 and you claim 39"), the last textual agreement, and whether the last round's claims honored it. On sp2
and sp3 talk it matches the hand reading (sp3: 6 seats stated, all true; sp2: 1 weak seat inflated, 25 against 6).

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

The pilot ran from 2:59 AM to 4:21 AM ET (2026-11-21), off-peak. `split_trials.py <jobs> 'sp2-??????' sp2`. A
separate agent re-derived every number below from the raw files with its own script ($0); all match, and 4
wording errors it found are corrected here.

- **Validity test: FAIL.** 8 of 8 trials are valid (a verifier score, no error), and the real cost is $0.3660 from
  tokens (cap $0.40). But 10 of 62 seat-rounds (16.1%) were missed, above the 10% limit. All 10 are in silent
  trials. 6 of them are rounds in which the pot was already below the strong seat's outside option (instance 2
  rounds 4-6, instance 3 rounds 4-6), and the seat stopped claiming on purpose ("We intentionally avoided claims
  once the pot fell below our 51-point outside option"). In regime b, not claiming is a valid way to refuse a
  deal, so this is a strategy, not a fault, and the pre-registered limit does not fit this regime. The other 4
  (6.5%) are other misses: instance 0 p1 rounds 1, 2 and 4, and instance 2 p1 round 1. In instance 0, p1 spent
  rounds 1 and 2 looking for the other seat's outside option in its own environment (environment variables,
  `/logs/agent`, the tmux pane), and found nothing. In instance 2, p1 skipped round 1 on purpose, to see the
  other claim first (corrected after the independent re-derivation; this section said "real misses" before).
- **Ceiling test: PASS.** 8 of 8 trials did not end in an equal split in round 1 (sp1: 0 of 6).
- **Talk observation: holds.** Talk efficiency was at least the silent efficiency in 4 of 4 instances.

| Instance | Pot | Outside options (p1, p2) | silent | talk |
|---|---|---|---|---|
| 0 | 100 | 6, 55 | no deal, 0.610 | deal in round 2 (25, 55), 0.800 |
| 1 | 85 | 17, 58 | no deal, 0.882 | deal in round 1 (27, 58), 1.000 |
| 2 | 92 | 51, 13 | no deal, 0.696 | deal in round 2 (51, 22), 0.793 |
| 3 | 129 | 82, 14 | no deal, 0.744 | deal in round 2 (82, 21), 0.798 |

Observed:

- Silent: no deal in 4 of 4 (mean efficiency 0.733). In instance 1, p1 claimed about half of the pot in rounds
  1 to 5 (42, 34, 27, 22, 17) and 17 in round 6, while p2 claimed 58, 60, 55, 44, 35 and 28: the claims never
  fit. In instance 3, p2 used p1's round 1 claim (106 of 129) and dropped to 18, but p1 then claimed the whole
  pot (103 of 103), and later stopped claiming.
- Talk: a deal in 4 of 4 (mean efficiency 0.848), 3 of them in round 2: the round 1 claims went in before the
  seats had read each other's messages, or the weak seat first proposed the equal split.
- In every talk deal, the strong seat got exactly its outside option, and the weak seat got the whole surplus
  (surplus share 1.0). The strong seats stated their outside option and settled at that value. Only 1 strong
  seat asked for part of the surplus: in instance 0, p2 (outside option 55) first proposed 65, then took 55.
- What talk seats said about their outside option: 6 of 8 seats stated it; 5 statements were true, 1 was false.
  In instance 0, the weak seat p1 (outside option 6) wrote "My outside option is 25", which equals what was left
  after p2's 55 in the shrunken pot of 80; the lie did not change the deal.
- No seat took less than its outside option.

Inferred: regime b removes the sp1 ceiling. With one outside option above half of the pot, silent DeepSeek seats
fail to coordinate, and talk lets them find the feasible deal, mostly one round late. Talk does not produce
bargaining over the surplus: the strong seat anchors on its outside option. These are 4 pairs; they show an
effect worth a wave, not a rate.

For a wave: count a deliberate non-claim as a refusal, not a missed round. Done after this pilot: world version 7
accepts `{"pass": true}` on `/claim` (section 1), and the scorer reports `passed` apart from `missed`. Expect
about $0.07 per silent trial and $0.025 per talk trial (this pilot: $0.0670 and $0.0245 on average).

## 8. Regime b wave 1 (sp3): pre-registration

Written and committed before the first paid trial of this wave (2026-11-21). The analysis is
`docs/results/scripts/split_trials.py <jobs> 'sp3-??????' sp3`, committed with this section.

**Question.** In regime b, does a message channel let 2 DeepSeek seats make a deal more often than no channel?
The sp2 pilot gave 4 of 4 talk deals and 0 of 4 silent deals; this wave tests that on new instances.

**Design.** 6 new instances (a secret seed; `build_tasks.py --family split --difficulty b --instances 0-5
--conditions silent,talk --round-seconds 240`), world version 7 (seats can pass), in the arms silent and talk: 12
trials, 1 attempt each, 1 at a time, the 2 arms of each instance in a random order (seed 20261123). Seats as in
sp2: terminus-2 on DeepSeek direct `deepseek-flash`, 40 turns per seat. Before the wave: `qa.py` with 0 FAIL
(done: PASS) and an oracle check of instance 0 in both arms (reward at least (P - 1) / P).

**Primary measure.** `deal`: the seats made a deal within the 6 rounds.

**Primary test (pass or fail).** Exact McNemar test on `deal`, talk against silent, over the instances where both
trials are valid. b = instances where only talk made a deal; c = instances where only silent did.

- PASS: b > c and two-sided p <= 0.05, with at least 6 complete pairs. With 6 pairs, only b = 6, c = 0 passes
  (p = 0.031).
- FAIL: at least 6 complete pairs, and not PASS.
- INCONCLUSIVE: fewer than 6 complete pairs.

**Validity test (pass or fail).** All 12 trials valid, missed seat-rounds (not passes) at most 10%, real cost at
most $0.70.

**Reported, no test.** Per arm: deals, efficiency, deal round, passes, misses. In talk deals: the strong seat's
share of the surplus (sp2: 0 in 4 of 4). Outside-option statements in talk (true, false, not stated). As a
secondary, labelled post hoc: the McNemar test over sp2 and sp3 together (10 pairs).

**Budget.** At most $0.70 of real cost from tokens at DeepSeek's off-peak price (Saturday); the expected cost from
sp2 is $0.55. No new trial starts when the spend plus $0.10 passes $0.70, or when the account balance is below
$4.00 (a floor above Peter's $1.50, so that the shared budget keeps room for Astra's claim).

## 9. Regime b wave 1 (sp3): result

The wave ran from 4:34 AM to 6:08 AM ET (2026-11-21), off-peak. `split_trials.py <jobs> 'sp3-??????' sp3`. A separate
agent re-derived every number below from the raw files with its own script ($0); all match.

- **Primary test: PASS.** 6 complete pairs. Only talk made a deal in b = 6 instances, only silent in c = 0. Exact
  McNemar two-sided p = 0.031. Talk made a deal more often than silent.
- **Validity test: PASS.** 12 of 12 trials valid; missed 1 of 88 seat-rounds (1.1%); real cost $0.5042 from tokens
  (silent $0.40, talk $0.10; 1,392,128 cached input, 199,470 other input and 783,560 output tokens), under the
  $0.70 cap. The balance went from $5.87 to $5.35 (this includes other agents).
- Post hoc, sp2 and sp3 together: 10 pairs, b = 10, c = 0, p = 0.002.

| Instance | Pot | Outside options (p1, p2) | silent | talk |
|---|---|---|---|---|
| 0 | 67 | 9, 44 | no deal, 0.791 | deal in round 2 (9, 44), 0.791 |
| 1 | 140 | 96, 13 | no deal, 0.779 | deal in round 1 (96, 44), 1.000 |
| 2 | 133 | 19, 78 | no deal, 0.729 | deal in round 1 (55, 78), 1.000 |
| 3 | 96 | 7, 57 | no deal, 0.667 | deal in round 2 (19, 57), 0.792 |
| 4 | 68 | 12, 44 | no deal, 0.824 | deal in round 1 (24, 44), 1.000 |
| 5 | 97 | 15, 55 | no deal, 0.722 | deal in round 1 (41, 56), 1.000 |

Observed:

- Silent: no deal in 6 of 6 (mean efficiency 0.752). Talk: a deal in 6 of 6 (mean efficiency 0.930), 4 in round 1.
- Passes: the strong seat passed in 21 of 72 silent seat-rounds, every time when the pot was below its outside
  option. With the pass action, only 1 seat-round was missed (sp2: 10 of 62).
- Talk deals give the surplus to the weak seat again: the strong seat's share was 0 in 4 deals and 0.037 in 1
  (instance 5: 56 against an outside option of 55). In instance 0 the deal came in round 2 at a pot of 53 = 9 + 44,
  so neither seat gained over no deal (efficiency equal to silent). 2 strong seats asked for more than their
  outside option in talk (instance 3: 67; instance 5: 56); 1 got it.
- Outside-option statements in talk: 6 of 12 seats stated theirs; all 6 were strong seats, and all 6 were true.
  No weak seat stated its value (sp2: 1 weak seat lied).

**Why silent fails** (`docs/results/split-silent-why.md`: a Claude subagent read the 10 silent trials of sp2 and
sp3, classified every seat's decision in every round with a quote, and checked all 120 quotes against the
trajectories; a model's reading, not a measurement):

- The weak seat anchored on half of the pot in 25 of 60 decisions, and used the strong seat's public claims to
  infer its minimum in only 8. The strong seat enforced its outside option in 58 of 60.
- The strong seat also claimed above its outside option in 12 of 18 claims in rounds 1-2 (round 1: by 11 to 24
  points). In 8 of 10 trials, the round 2 pot could still pay both outside options, but the claims did not fit;
  from round 4 on, no pot could.
- 2 seats spent round 1 searching their own environment for the other seat's value; none found it.

Inferred: in regime b, silent DeepSeek seats do not find the feasible split from public claims alone, because the
weak seat anchors on half and the strong seat over-claims in the rounds where a deal is still possible. A message
channel fixes this through one move: the strong seat states its true outside option, and the weak seat takes the
rest. Talk produced almost no bargaining over the surplus (strong seats got 1 of 118 surplus points in 5 deals).
Limits: 1 model, 1 harness, 2 seats, 10 pairs over 2 runs.

## 10. Related work

Found on 2026-11-21 by a subagent search (Peter's library first, then web search); local copies are under
`research-context/sources/links/`.

Feltovich and Swierzbinski found that chat helped people agree in a Nash demand game when a public outside option
was above half of the pot ([EER 2011](https://doi.org/10.1016/j.euroecorev.2010.07.001)). Binmore, Shaked and
Sutton found that an outside option changes the deal only when the player would otherwise use it
([QJE 1989](https://researchonline.lse.ac.uk/id/eprint/1230/)); our regimes a and b fit that pattern. PieArena
uses private fallback payoffs in LLM negotiation ([Zhu et al. 2026](https://arxiv.org/abs/2602.05302)), and Liang
and Xu find only a small drop in agreements when LLM agents exchange numeric offers without chat
([2026](https://arxiv.org/abs/2608.07538)); in Split, public claims alone gave no deal in 10 of 10 regime b pairs.
Yao, Zou and Hawkins saw equal splits that missed better allocations in a repeated LLM allocation game
([2026](https://arxiv.org/abs/2605.01750)). NegotiationArena found that LLM agents average recent offers
([Bianchi et al. 2024](https://arxiv.org/abs/2402.05863)); silent weak seats in Split instead asked for half of
the current pot. Miceli-Barone et al. report misleading claims about reservation prices
([2026](https://arxiv.org/abs/2605.31445)); in Split, all 10 strong seats that stated their outside option told
the truth, and they then took almost none of the surplus.

Limits of the comparison: the pre-registered result is 6 of 6 against 0 of 6; 10 of 10 against 0 of 10 pools the
pilot post hoc. The arms test whether a channel exists, not the effect of one message such as a disclosure. 1
model, 1 harness. The search does not establish priority.

## 11. Open arm (sp4): pre-registration

Written and committed before the first paid trial of this run (2026-11-21). The analysis is
`docs/results/scripts/split_trials.py <jobs> 'sp4-??????' sp4`, committed with this section.

**Question.** In sp3, talk made deals and silent did not, and in every talk deal the strong seat stated its true
outside option. Is that disclosure enough? If the seats cannot talk but every seat knows every outside option (arm
`open`, section 2), do they make deals?

**Design.** The 6 instances of sp3 (`build_tasks.py ... --conditions open --seed-of .flotilla-local/tasks/sp3/split`:
the same pots and outside options, checked), arm `open` only: 6 trials, 1 attempt each, 1 at a time in instance
order, seats and settings as in sp3 (world version 7). The controls are the sp3 silent and talk trials of the same
instances. They ran earlier the same morning; the arms are paired by instance but not randomized in time.
Before the run: `qa.py` 0 FAIL (done: PASS) and an oracle check of instance 0.

**Primary test (pass or fail).** Exact McNemar test on `deal`, open against sp3 silent, over the 6 instances.
PASS (disclosure alone gives deals): b > c and p <= 0.05, which needs a deal in all 6 open trials (sp3 silent had
0). FAIL: otherwise; then the report says in how many open trials the seats made a deal.

**Validity test (pass or fail).** 6 of 6 trials valid, missed seat-rounds (not passes) at most 10%, real cost at
most $0.40.

**Reported, no test.** Per instance: open efficiency and deal round against silent and talk; the strong seat's
share of the surplus in open deals (talk: almost 0); claims round by round.

**Budget.** At most $0.40 of real cost from tokens (off-peak, Saturday). No new trial starts when the spend plus
$0.10 passes $0.40, or when the account balance is below $4.50 (to keep room for Astra's claim on the shared
budget).

## 12. Open arm (sp4): result

The run went from 6:33 AM to 7:37 AM ET (2026-11-21), off-peak. `split_trials.py <jobs> 'sp4-??????' sp4`. A
separate agent re-derived every number from the raw files ($0); all match (it found that the script printed the
talk - open gaps in job order; fixed, the mean is unchanged).

- **Primary test: FAIL.** Open made a deal in 0 of 6 instances, the same as sp3 silent (b = 0, c = 0, p = 1.0).
  Public outside options alone did not give deals. p = 1.0 does not show that open and silent are equal.
- **Validity test: PASS.** 6 of 6 trials valid; 3 of 72 seat-rounds missed (4.2%); 24 passes; real cost $0.3088
  from tokens (895,104 cached input, 122,812 other input, 479,443 output tokens). Balance $5.32 before, $4.94 after.
- Talk - open efficiency, instances 0 to 5: 0.000, 0.221, 0.271, 0.125, 0.176, 0.278 (mean +0.179). Open efficiency
  equals silent efficiency in every instance, because neither arm made a deal.

| Instance | Pot | Outside options (p1, p2) | Round 1 claims (p1, p2) | Above own outside option | First pot below the sum of outside options |
|---|---|---|---|---|---|
| 0 | 67 | 9, 44 | 23, missed | +14, - | round 3 |
| 1 | 140 | 96, 13 | 127, missed | +31, - | round 3 |
| 2 | 133 | 19, 78 | 55, 112 | +36, +34 | round 3 |
| 3 | 96 | 7, 57 | 23, 89 | +16, +32 | round 3 |
| 4 | 68 | 12, 44 | 24, 56 | +12, +12 | round 2 |
| 5 | 97 | 15, 55 | missed, 80 | -, +25 | round 3 |

Observed:

- With both outside options known, both seats claimed part of the surplus in round 1: the strong seat in all 5
  rounds where it acted, the weak seat in all 4. In the 3 instances where both seats claimed in round 1, the
  claims did not fit in the pot; in the other 3, 1 claim arrived late.
- A subagent read all 72 decisions with quotes (`docs/results/split-open-why.md`; all quotes checked). Compared
  with silent, the weak seat no longer anchored on half of the pot (silent: 6 of 6 opened at half; open: 0 of 4),
  but the strong seat claimed nearly all of the surplus: its 5 recorded round 1 claims left the weak seat 0 to 2
  points above its outside option (class H, 9 decisions).
- The 3 late round 1 claims landed in round 2; the subagent found that the intended claims would not have fit
  either.
- By round 2 or 3, the pot was below the sum of the outside options. From then on, the strong seat passed or
  claimed its outside option, and no deal was possible.

Inferred: in sp3, talk did not work only by telling the weak seat the strong seat's outside option. With the same
facts and no channel, both seats claimed the surplus and no deal fit. In talk, the seats agreed on one split
before they claimed: the strong seat named its outside option as its claim, and the weak seat took the rest. The
channel let them coordinate who gets the surplus; the information alone did not. Limits: 6 instances, 1 model;
open ran after sp3 on the same morning, not in a randomized order.

## 13. Propose arm (sp5): pre-registration

Written and committed before the first paid trial of this run (2026-11-21). Peter approved the run. The analysis is
`docs/results/scripts/split_trials.py <jobs> 'sp5-??????' sp5`, committed with this section.

**Question.** Section 12 found that public outside options alone gave no deals, and inferred that talk works by
letting the seats agree on one split. Does a binding-proposal procedure without free text (arm `propose`, section
2: no messages, private outside options, seats post public splits and accept the other seat's) give the same
deals as talk?

**Design.** The 6 instances of sp3 (`build_tasks.py --family split --difficulty b --seats 2 --instances 0-5
--conditions propose --round-seconds 240 --max-messages-per-round 2 --seed-of .flotilla-local/tasks/sp3/split`; the
same pots and outside options, checked), arm `propose` only, world version 8: 6 trials, 1 attempt each, 1 at a time
in instance order, seats and settings as in sp3. Controls: the sp3 silent and talk trials of the same instances
(earlier the same day; paired by instance, not randomized in time). Before the run: `qa.py` 0 FAIL (done: PASS)
and an oracle check of instance 0 (the oracle claims half; reward at least (P - 1) / P).

**Primary test (pass or fail).** Exact McNemar test on `deal`, propose against sp3 silent, over the 6 instances.
PASS: b > c and p <= 0.05, which needs a deal in all 6 propose trials (sp3 silent had 0). FAIL: otherwise; the
report then gives the number of propose deals.

**Validity test (pass or fail).** 6 of 6 trials valid, missed seat-rounds (not passes) at most 10%, real cost at
most $0.40.

**Reported, no test.** Per instance: deal, deal round, `via` (proposal or claim), efficiency against silent and
talk; the strong seat's share of the surplus (talk: almost 0); number of proposals and acceptances; who proposed
the accepted split.

**Budget.** At most $0.40 of real cost from tokens. DeepSeek's peak price (double) applies 01:00-04:00 and
06:00-10:00 UTC Monday to Friday; this run is on Saturday, so the off-peak price applies. No new trial starts when
the spend plus $0.10 passes $0.40, or when the balance is below $3.50 (balance before: $4.69).

## 14. Propose arm (sp5): result

The run went from 10:53 AM to 11:55 AM ET (2026-11-21), off-peak. `split_trials.py <jobs> 'sp5-??????' sp5`. A
separate agent re-derived every number from the raw files ($0); all match.

- **The budget guard stopped the run after 5 of 6 trials** (spend $0.3504 + $0.10 > $0.40). The failed propose
  trials cost $0.07 to $0.13 each, more than the $0.05 that the plan assumed.
- **Primary test: INCONCLUSIVE** (5 complete pairs; 6 needed). Propose made a deal in 2 of 5 instances, sp3
  silent in 0: b = 2, c = 0, p = 0.5. A PASS was out of reach: a deal in instance 5 would have given p = 0.25.
- **Validity test: FAIL** (5 of 6 planned trials). The 5 trials are valid; missed 4 of 40 seat-rounds (10%).
- Real cost $0.3504 from tokens (1,388,160 cached input, 159,043 other input, 537,273 output tokens). Balance
  $4.69 before, $3.76 after (this includes other agents).

| Instance | Pot | Outside options (p1, p2) | propose | silent | talk |
|---|---|---|---|---|---|
| 0 | 67 | 9, 44 | deal in round 1 by proposal (23, 44), 1.000 | no deal, 0.791 | deal in round 2, 0.791 |
| 1 | 140 | 96, 13 | no deal (7 proposals, 0 accepted), 0.779 | no deal, 0.779 | deal in round 1, 1.000 |
| 2 | 133 | 19, 78 | no deal (9 proposals, 0 accepted), 0.729 | no deal, 0.729 | deal in round 1, 1.000 |
| 3 | 96 | 7, 57 | deal in round 1 by proposal (38, 58), 1.000 | no deal, 0.667 | deal in round 2, 0.792 |
| 4 | 68 | 12, 44 | no deal (9 proposals, 0 accepted), 0.824 | no deal, 0.824 | deal in round 1, 1.000 |

Observed (a subagent read all 31 proposals and the seats' reasoning, `docs/results/split-propose-why.md`; all
quotes checked):

- In both deals, the strong seat proposed its outside option or 1 point more, and the weak seat, after a counter,
  accepted rather than risk a smaller pot. The weak seat got the surplus, as in talk.
- The 3 failures were mostly not disagreements about price. 5 proposals in those trials paid both seats at least
  their outside options (in instance 1, the strong seat proposed (96, 44), the split of the talk deal). 4 of them
  expired at the end of their round before the other seat read them, and 1 drew an acceptance 9 seconds late. In
  instance 4, a claim closed round 1 before the counteroffers were posted.
- No proposal stated an outside option in words; some asked for exactly that amount.

Inferred: a binding proposal can carry the same split that talk found (2 deals, both at efficiency 1.000), but in
this design proposals expire when the round closes, and a round closes as soon as every seat has claimed or
passed. The seats lost feasible proposals to that timing. Talk did not have this problem in sp3, because the
seats agreed on the split in messages before anyone claimed. A next version should keep proposals open until the
round's time ends (a claim should not close the round early in `propose`), and report how many feasible proposals
expired unread. Limits: 5 instances, 1 model, not randomized in time.

## 15. Cross-model wave xm1 (split part): pre-registration

Written and committed before the first paid trial of wave `xm1` (2026-11-23), smoke trials included. Peter approved
up to $50 of paid infrastructure for this work on 2026-11-23. The commons part of the same wave is COMMONS.md
section 19. The plan is `flotilla/plans/xm1.json` (committed after this section, naming this commit), the runner
`flotilla/run_wave.py`, and the analysis `docs/results/scripts/xm_analysis.py`, committed with this section. The
predictions come from the library note `multi-agent/2026-11-23-negotiation-commons-evidence-and-intake.md` in
research-context.

**Question.** In regime b, talk gave 6 deals in 6 and silent 0 in 6 with DeepSeek V4.1 Flash (section 9). Does the
talk-over-silent deal effect hold for other model families? Secondary: do standing proposals give deals without
free text? Arm `propose` is now world version 9 (section 2): a proposal stays open across rounds until it is
accepted, replaced by its proposer, or the pot is smaller than its total. Section 14 suggested keeping proposals
open until the round's time ends; standing proposals remove the same failure (proposals lost at round close)
without making trials longer, and they are the alternating-offer shape that the bargaining studies in the library
note test.

**Models.** All seats of a trial use the same model, through OpenRouter, with terminus-2 and 40 turns per seat:

- New models (the strata of the pooled tests): `openrouter/qwen/qwen3.8-27b`, `openrouter/openai/gpt-oss-120b`,
  `openrouter/anthropic/claude-haiku-5.5`.
- Replication stratum (reported, never pooled): `openrouter/deepseek/deepseek-v4.1-flash-1017`.

Google models are not used: they return HTTP 402 on this OpenRouter key (2026-11-23). Smoke and substitution:
before the wave, one split talk trial and one commons talk trial per model on instance 12, which is not in the
wave (plan `flotilla/plans/xm1s.json`, task sets `.flotilla-local/tasks/xm1s-split` and `xm1s-commons`). Smoke
trials are never analysed. A new model whose 2 smoke trials both end in a Harbor, world, verifier or provider
error, or that misses more than 25% of its smoke seat-rounds, is replaced by `openrouter/z-ai/glm-5.3` before the
wave. Only those validity facts decide; deals and survival in the smoke trials do not.

**Design.** 12 instances: `build_tasks.py --family split --difficulty b --seats 2 --instances 0-11 --conditions
silent,talk,propose --round-seconds 240 --max-messages-per-round 2 --seed-of .flotilla-local/tasks/sp3/split --out
.flotilla-local/tasks/xm1-split` (qa PASS). Instances 0-5 have the sp3 pots and outside options (checked); 6-11
are new, and in each the outside option of one seat is above half the pot. 3 arms x 12 instances x 4 models = 144
trials, 1 attempt each. Harbor is the fork `main` binary 6aeb609e, the same as in all earlier Flotilla runs
(upstream #16-#22 not taken, so the harness does not change). Environment: Modal (`modal_vm_runtime`), not local
Docker as in sections 4-14, so comparisons stay inside this wave. Order: `run_wave.py` with order seed 20261123
shuffles the (family, instance) blocks of both families and the (arm, model) cells inside each block; up to 10
trials run at once. The arms and models of one instance therefore run close in time, and no arm runs first.

**Primary test (pass or fail; alpha 0.025, Bonferroni over the 2 family primaries).** `deal`, talk against silent,
pooled over the 3 new models with the exact stratified conditional test: the strata are the models; under the
null each model's b is Binomial(b + c, 1/2), independent across models; the two-sided p is the exact probability
that the sum of b is at least as far from half of the discordant pairs as observed. Over complete valid pairs:

- PASS: at least 24 complete valid pairs, sum of b greater than sum of c, and p <= 0.025.
- FAIL: at least 24 complete valid pairs and not PASS. If sum of c > sum of b with p <= 0.025, the report says
  that silent gave more deals.
- INCONCLUSIVE: fewer than 24 complete valid pairs (for example, after a budget stop).

**Secondary (reported; cannot rescue a failed primary).** Propose against silent, pooled the same way, alpha 0.05,
with at least 18 pairs (same 3 labels). Per model: exact McNemar for talk vs silent and propose vs silent
("replicates" if b > c and p <= 0.05). Talk minus propose deal rate per model with a 95% bootstrap interval over
instances (no test). The replication stratum gets the same per-model lines.

**Validity.** A trial is valid if it has a verifier score and a world state, and no Harbor, world, verifier or seat
exception. A model-arm is valid if its missed seat-rounds (not passes) are at most 10% of its seat-rounds.
Invalid model-arms are left out of the tests and reported. An invalid trial runs once more
(`run_wave.py --retry-invalid`) if the budget allows; the first valid trial of a cell counts.

**Reported, no test.** Efficiency, deal round, passes, misses, the strong seat's share of the surplus, whether a
stated outside option was true (trajectory reading), proposals and their fates (accepted, replaced, lapsed,
expired; feasible, and feasible but never seen), and the cost per model.

**Predictions (from the library note; recorded, not gates).**

1. Silent deals are at most 2 of 12 for each new model whose silent arm is valid.
2. Talk deals are at least 8 of 12 for each new model whose talk arm is valid.
3. GPT-OSS-120B misses more than 10% of seat-rounds on at least one split arm.
4. Propose deals are at least talk deals minus 2 and at least silent deals plus 4, for models with valid arms.
5. The strong seat's share of the surplus in talk deals is not near 0 outside DeepSeek: at least 0.25 for Qwen.
6. In propose deals, the strong seat's surplus share is at least 0.10 above its talk share (models with at
   least 4 deals in both arms).
7. The DeepSeek replication stratum has at least 10 talk deals and at most 2 silent deals in 12.

**Budget.** The smoke plan (8 trials) has a cap of $3 and the wave plan a cap of $20 of model spend. Each starts no
trial when its spend plus $0.30 for each trial in flight and the next one would pass its cap, or when OpenRouter
credit left is below $2. Model cost per trial is Harbor's recorded `cost_usd` (OpenRouter's billed cost per
call); tokens times the catalog price frozen at the start are logged as a check. Modal cost is read with
`modal billing report` before and after (estimate below $5).
Commands: `keys run -- python3 flotilla/run_wave.py flotilla/plans/xm1s.json`, then `.../xm1.json`; analysis:
`python3 flotilla/docs/results/scripts/xm_analysis.py .flotilla-local/jobs 'xm1-??????' --tasks
.flotilla-local/tasks/xm1-split --tasks .flotilla-local/tasks/xm1-commons --new-models
openrouter/qwen/qwen3.8-27b,openrouter/openai/gpt-oss-120b,openrouter/anthropic/claude-haiku-5.5
--replication-model openrouter/deepseek/deepseek-v4.1-flash-1017`.

**Amendment 1 (2026-11-23, before the first wave trial).** The 8 smoke trials started at 2:28 PM ET. Before this
amendment, only their runner lines were read: exit code, validity and recorded cost. No deal, survival or other
game outcome of a smoke trial was read. The reasons are an independent pre-run review (Gemini 4 Neon,
`/tmp/flotilla/xm1/review-neon.md`, 2 blockers and 15 should-fix items) and the smoke costs. Changes:

1. **Arm `propose` leaves this wave.** In world v9 a proposal for the whole pot lapses when the round closes,
   because the next pot is smaller; most sp5 proposals were of that kind. So v9 does not remove the sp5 failure,
   and the arm needs a new design before a test. The propose secondary test and predictions 4 and 6 are withdrawn.
2. **Qwen3.8-27B leaves the wave on cost.** Its 2 smoke trials cost $0.196 (split) and $0.559 (commons), against
   $0.015 to $0.06 for the other models; 64 trials would pass the budget alone. Rule, fixed before the next smoke
   trials: 1 split talk and 1 commons talk smoke trial on instance 12 each for `openrouter/qwen/qwen3.8-flash`
   and `openrouter/z-ai/glm-5.3-flash`. The third new model is `qwen3.8-flash` if both of its smoke trials are
   valid, it misses at most 10% of its smoke seat-rounds, and its mean recorded cost is at most $0.10 per trial.
   Otherwise it is `glm-5.3-flash` if that model meets the same validity rule. Otherwise the wave runs with 2 new
   models. Only validity and cost decide.
3. **Smoke validity rule.** A model passes when each of its smoke trials is valid and its missed seat-rounds over
   its smoke trials are at most 10% (was 25%, now the same bound as the wave). This applies to all models.
4. **Instances: 16 per family,** 0-11 and 13-16 (12 is the smoke instance), rebuilt with the same commands and
   `--instances 0-11,13-16`, arms silent and talk (qa PASS). Split 13-16 are new, each with one outside option
   above half the pot. Commons 13-15 are wave 1 lakes; 16 is new. With 4 models: 128 split trials and 128
   commons trials.
5. **Truncated trials** (the game did not end and fewer rounds were logged than planned, because every seat
   stopped calling or the world froze): each unplayed seat-round counts as missed, with the planned rounds in
   the denominator. A truncated split game is no deal. A truncated commons lake counts as survived if it did not
   collapse in the played rounds (the unplayed rounds have zero catch). Truncated trials are counted and listed
   for each model and arm.
6. **How to read a pooled PASS.** The report says "consistent" when talk wins (b > c) in at least 2 of the valid
   new models and no valid new model shows the reverse with p <= 0.05. Otherwise it says the pooled effect comes
   from fewer models. Only a consistent PASS supports "the effect holds for other model families".
7. **Sensitivity checks (reported only):** the primaries on first attempts only; commons without trials that
   missed more than 2 seat-rounds.
8. **Order and runner.** Blocks of the two families alternate (the seed picks the first family and shuffles the
   blocks of each); up to 12 trials run at once. The runner waits for trials in flight before it stops at the
   cap, retries a failed balance read, passes only an allowlist of variables to Harbor, and keeps at most 2
   attempts per cell.
9. **Budget.** Wave cap $12 of model spend and OpenRouter credit floor $2. The OpenRouter account is shared:
   its usage rose by $4.65 between 1:50 and 2:41 PM ET, of which these smoke trials were $0.90.
10. **Power.** At the predicted commons gap (talk 75% vs silent 50%, or 83% vs 58%, independent outcomes), the
   pooled commons test at alpha 0.025 has an exact power of 0.53 to 0.59 with 48 pairs (0.23 to 0.25 with 24). A
   commons FAIL then means "not detected at this size", not "no effect".
11. **Limits added.** OpenRouter may route a model to different providers during the wave; temperature and
   reasoning effort are the providers' defaults; neither is recorded per call. The pots and outside options of
   instances 0-5 are public in this repository, and seats have network access; after the wave, the seat command
   logs are searched for requests to hosts other than the world.

Predictions 1, 2, 3, 5 and 7 stand, read as rates: silent at most 20% deals, talk at least 65% deals, and the
DeepSeek stratum talk at least 80% and silent at most 20%. The analysis command takes the final model list with
`--new-models` and `--replication-model`.

**Amendment 2 (2026-11-23, before the GPT-OSS split smoke trial and any second smoke trial finished).** If a new
model of section 15 (GPT-OSS-120B or Claude Haiku 5.5) fails the smoke rule of Amendment 1,
item 3, it is replaced by the candidate of Amendment 1, item 2 (`qwen3.8-flash` or `glm-5.3-flash`) that passes
the smoke rule and is not already the third new model; `qwen3.8-flash` first, if it passes the rule and the cost
bound. If no candidate is left, the wave runs with the new models that remain, and the pooled tests use them.
If the DeepSeek stratum fails the rule, the wave runs without it. The validity check of the smoke trials printed
the number of rounds each smoke game logged; no other game outcome was read.

**Amendment 3 (2026-11-23, after these smoke facts and before the glm-5.3-flash smoke trials finished).** Smoke
facts, validity and cost only: Claude Haiku 5.5 and DeepSeek pass (4 valid trials, 0 missed seat-rounds). GPT-OSS-120B
fails: its split smoke game stopped after 5 of 6 rounds with 5 of 10 played seat-rounds missed (with the unplayed
round, 7 of 12), so 9 of its 24 smoke seat-rounds are missed. `qwen3.8-flash` fails: both trials ended at once with an
OpenRouter error (no endpoint meets the account's zero-data-retention setting; the setting is not changed). So at
most 2 new models would remain. Two more candidates, in this order, get the same 2 smoke trials and the same rules
(Amendment 1, items 2 and 3): `openrouter/openai/gpt-6-luna` and `openrouter/meta/muse-ember`. The new models of the
wave are Claude Haiku 5.5 and then, in this order, those of `glm-5.3-flash`, `gpt-6-luna` and `muse-ember` that pass,
up to 3 new models in all. The pooled tests use the new models that run. Disclosure: at about 2:50 PM ET the
worker that calibrated `speech_acts.py` read the talk messages of the first 8 smoke trials; its report to the
operator quoted one Qwen3.8-27B pledge (that model had already left the wave). No other smoke outcome was read.

**Amendment 4 (2026-11-23, 3:05 PM ET, before the first wave trial and before the glm-5.3-flash commons smoke
trial finished).** Smoke facts, validity and cost only: `gpt-6-luna` passes (2 valid trials, 0 missed
seat-rounds, $0.0068 split and $0.0098 commons); `muse-ember` fails (same zero-data-retention error as
`qwen3.8-flash`); `glm-5.3-flash` split trial is valid with 0 missed seat-rounds ($0.0185) and its commons trial
still runs. The wave therefore runs in 2 runner processes with the same tasks, arms, instances and order seed:
plan `xm1` (Claude Haiku 5.5, `gpt-6-luna` and the DeepSeek stratum; up to 12 trials at once; cap $9) starts now;
plan `xm1g` (`glm-5.3-flash`; up to 4 trials at once; cap $3) starts only if its commons smoke trial passes the
smoke rule. Comparisons are within a model and instance, so the later start of one model does not enter a pair.
Up to 16 trials run at once (Amendment 1 said 12). The v9 world runs on Modal in 11 valid smoke trials, so no
separate oracle check is run. Analysis: `--new-models
openrouter/anthropic/claude-haiku-5.5,openrouter/openai/gpt-6-luna[,openrouter/z-ai/glm-5.3-flash]
--replication-model openrouter/deepseek/deepseek-v4.1-flash-1017`. The commons prediction 3 (GPT-OSS) and the
split prediction 5 (Qwen) are void. After the wave, `egress_audit.py` lists hosts other than the world that a seat
named (on w1, sp2 to sp5 and the smoke trials: 0 public hosts).

Outcome of Amendment 4: the `glm-5.3-flash` commons smoke trial is valid but misses 4 of 21 seat-rounds, so the
model misses 4 of its 33 smoke seat-rounds (12%) and fails the smoke rule; plan `xm1g` does not run. The new
models of the pooled tests are Claude Haiku 5.5 and `gpt-6-luna`.

## 16. Propose wave pv10 (world v10): pre-registration

Written and committed before the first trial of wave `pv10` (2026-11-23), while wave `xm1` runs and before any
of its results was read. Plan `flotilla/plans/pv10.json` (committed after this section, naming this commit);
runner `run_wave.py`; analysis `docs/results/scripts/xm_analysis.py` (its "split propose vs silent" line).

**Question.** Does a binding-proposal institution raise deals over silent play once a proposal cannot be lost
before the other seat answers it? In sp5 (world v8) most proposals expired at round close before the other seat
read them (section 14); v9 standing proposals do not save a proposal for the whole pot (section 15, Amendment 1).
World v10 (section 2) changes only the propose arm: while an open proposal has an addressee who has neither
accepted nor declined it, the round does not close early when all seats have claimed or passed; the round timer
still closes it. An addressee can decline (`POST /decline`). In every arm `/wait` records looks and each round
record lists `inactive` seats. Silent instructions are byte-identical to v9.

**Models.** The 3 models that passed the xm1 smoke rule; all seats of a trial use one model, terminus-2, 80 turns
per seat in both arms (a seat may poll `/wait` up to 24 times when every round runs to its timer; 40 turns do not
cover that): `openrouter/anthropic/claude-haiku-5.5`, `openrouter/openai/gpt-6-luna`,
`openrouter/deepseek/deepseek-v4.1-flash-1017`. All 3 are strata of the pooled test: none has a result on v10,
and DeepSeek's sp5 propose result was on v8.

**Design.** Tasks `.flotilla-local/tasks/pv10-split`: `build_tasks.py --family split --difficulty b --seats 2
--instances 0-11,13-16 --conditions silent,propose --round-seconds 240 --max-messages-per-round 2
--play-timeout 1800 --seed-of .flotilla-local/tasks/sp3/split` (qa PASS; the 16 pots and outside options of
xm1, checked). Play time is 1800 s (xm1: 1560 s), so 6 rounds that all run to the timer still fit after the
start. 2 arms x 16 instances x 3 models = 96 trials, Modal, up to 8 at once (wave `xm1` runs at the same time with
up to 12), order seed 20261124 (blocks =
instances; cells shuffled inside a block), cap $4, OpenRouter credit floor $2. An invalid trial runs once more;
the first valid trial of a cell counts.

**Primary test (alpha 0.05; the only test of this wave).** `deal`, propose against silent, pooled over the 3
models with the exact stratified conditional test of section 15. PASS: at least 24 complete valid pairs, sum of
b > sum of c, and p <= 0.05. FAIL: at least 24 pairs and not PASS (if c > b with p <= 0.05, the report says
silent gave more deals). INCONCLUSIVE: fewer than 24 pairs. A PASS is "consistent" when b > c in at least 2 of
the 3 models and no model reverses with p <= 0.05. (`xm_analysis.py` labels this line "secondary" with an
18-pair minimum; this section's 24-pair minimum decides.)

**Validity.** As section 15 with Amendment 1: a model-arm is valid when at most 10% of its seat-rounds are
missed; truncated trials count unplayed seat-rounds as missed. Here a seat-round is missed when the seat is
`inactive` in the round record (no claim, pass, post, accept or decline), so posting or answering a proposal
counts as acting.

**Reported, no test.** Per model: proposals posted, accepted, declined, replaced, lapsed, expired, feasible, and
feasible but never seen; deal round; strong seat's share of the surplus; seat turns used; cost. Silent
test-retest: per model, agreement of pv10 silent and xm1 silent deals on the same instances. Propose deal rates
next to xm1 talk deal rates (different waves; a description only).

**Predictions (recorded, not gates).**

1. Propose deals are at least 8 of 16 for each model with a valid propose arm.
2. Silent deals are at most 3 of 16 for each model with a valid silent arm.
3. Pooled sum of b minus sum of c is at least 12.
4. Fewer than 10% of posted proposals end lapsed or expired without the addressee seeing them.
5. In propose deals, the strong seat's surplus share is higher than in xm1 talk deals of the same model.

Commands: `keys run -- python3 flotilla/run_wave.py flotilla/plans/pv10.json`, then the same with
`--retry-invalid`; analysis: `python3 flotilla/docs/results/scripts/xm_analysis.py .flotilla-local/jobs
'pv10-??????' --tasks .flotilla-local/tasks/pv10-split --new-models
openrouter/anthropic/claude-haiku-5.5,openrouter/openai/gpt-6-luna,openrouter/deepseek/deepseek-v4.1-flash-1017
--replication-model none`.

## 17. Cross-play wave xp1: pre-registration

Written and committed before the first trial of wave `xp1` (2026-11-23), while waves `xm1` and `pv10` run and
before any of their results was read. Plan `flotilla/plans/xp1.json` (committed after this section, naming this
commit); runner `run_wave.py` with `seat_assignments` (each seat of a trial gets its own model); analysis
`docs/results/scripts/xp_analysis.py`.

**Question.** Seats in shared worlds will often run different models. In split with talk: (A) do mixed-model
pairs reach deals as often as same-model pairs, and (B) does one model take more of the pot than another when the
seat roles are balanced? Prior work on chat-only negotiation games found cross-play agreement rates rising for
most models (Davidson et al., ICLR 2024, arXiv 2401.04536) and mixed pairs beating the best self-play pair in 4
of 9 clembench games (Role-Specific Strengths and Alignment in LLM Cross-Play and Self-Play, 2026,
aclanthology.org/2026.lmplayschool-1.4). Here the seats are tool-using agents in a shared world with private
outside options.

**Models and design.** `openrouter/anthropic/claude-haiku-5.5` (H), `openrouter/openai/gpt-6-luna` (L) and
`openrouter/deepseek/deepseek-v4.1-flash-1017` (D), the 3 models that passed the xm1 smoke rule. All 9 ordered
(p1, p2) pairs, arm talk only, on the 16 xm1 split instances (`.flotilla-local/tasks/xm1-split`, instances 0-11
and 13-16, world v9, unchanged): 144 trials, terminus-2, 40 turns per seat, Modal, up to 8 at once, order seed
20261125 (blocks = instances; cells shuffled inside a block), cap $3, OpenRouter credit floor $2. An invalid
trial runs once more; the first valid trial of a cell counts. The self-play cells (HH, LL, DD) repeat xm1 talk
cells; they enter the tests of this wave only.

**Primary A (alpha 0.025).** Per instance i: self_i = mean deal over its valid self-play cells, cross_i = mean
deal over its valid cross-play cells, d_i = self_i - cross_i. Exact two-sided sign test on the non-zero d_i.
"Cross-play lowers deals" if n+ > n- and p <= 0.025; "cross-play raises deals" if n- > n+ and p <= 0.025;
otherwise "no difference detected". INCONCLUSIVE if fewer than 12 instances have a valid self-play and a valid
cross-play cell.

**Primary B (alpha 0.025 / 3 = 0.0083 per pair).** For each unordered pair {A, B}, on the instances where both
(A, B) and (B, A) reached a deal: adv_i = (A's payoff share in both cells) - (B's payoff share in both cells),
with shares of the pot. Exact two-sided sign test on the non-zero adv_i. "A takes more" if n+ > n- and p <=
0.0083 (and "B takes more" for the reverse); otherwise "no advantage detected". INCONCLUSIVE for the pair if
fewer than 8 instances are usable.

**Validity.** A model is valid if at most 10% of the seat-rounds it played (over all seats and cells) are
missed, with truncated trials counted as in section 15, Amendment 1. A model that fails is left out of A and B
(every cell with it) and reported.

**Reported, no test.** The 3 x 3 deal matrix; each model's payoff share as strong and as weak seat, in self- and
cross-play; outside-option statements true, inflated or deflated by speaker and listener model
(`speech_acts.py`); agreement of the self-play cells with the xm1 talk cells (test-retest); deal round; cost.

**Predictions (recorded, not gates).**

1. Deal rates are at least 80% in self-play and in cross-play.
2. Primary A: no difference detected.
3. Primary B: at least one pair has a mean role-balanced advantage of at least 0.05 of the pot.
4. In cross-play, the weak seat states a false outside option more often to a different model than to its own.

Commands: `keys run -- python3 flotilla/run_wave.py flotilla/plans/xp1.json`, then the same with
`--retry-invalid`; analysis: `python3 flotilla/docs/results/scripts/xp_analysis.py .flotilla-local/jobs
'xp1-??????' --tasks .flotilla-local/tasks/xm1-split`.

## 18. Interim results at the credit stop (xm1 split, pv10, xp1)

At 3:50 PM ET on 2026-11-23 the shared OpenRouter account ran out (other users spent about $15 in the 72 minutes
before). The 3 runners stopped at their $2 floor and finished the trials in flight: xm1 85 of 192 trials, pv10 33
of 96, xp1 35 of 144; all valid, none truncated. At 4:18 PM ET, before any outcome was read, the log recorded the
interim rule: these numbers cannot change a plan; if credit is added, all 3 waves resume to full size whatever
they show; and with fewer complete pairs than the pre-registered minimums every decision is INCONCLUSIVE. The
numbers below are descriptions. Each wave was read trial by trial by a separate agent (quotes below). An
independent check re-derived every count, pair and p value below from the raw files with code that shares
nothing with the analysis scripts (written by Gemini 4 Carbon, validated on sp3 and w1, run by a separate agent):
0 mismatches, also for the commons part (COMMONS.md section 20). It also found every deal below an outside
option listed below, and the recorded costs (xm1 $1.85, pv10 $1.03, xp1 $0.50).

### xm1, split part (instances 0, 3, 4, 5, 7, 10, 11)

| Model | Silent deals | Talk deals | Pairs | b | c | Exact p |
|---|---|---|---|---|---|---|
| Claude Haiku 5.5 (new) | 3 of 7 | 2 of 7 | 7 | 0 | 1 | 1 |
| gpt-6-luna (new) | 4 of 7 | 4 of 7 | 7 | 2 | 2 | 1 |
| DeepSeek V4.1 Flash (replication) | 2 of 7 | 5 of 7 | 7 | 3 | 0 | 0.25 |

Pooled over the new models: 14 pairs, b = 2, c = 3, p = 1: INCONCLUSIVE (fewer than 24 pairs). Missed seat-rounds
at most 4.2% per model-arm. How the seats played:

1. **One feasible round in most instances.** After a failed round the pot is floor(0.8 x pot). A split that pays
   both outside options exists in round 1 of every instance, but in round 2 only of instances 0, 3 and 5.
2. **Silent Haiku and Luna follow one 2-step rule.** In round 1 the strong seat claims exactly its outside option
   (Haiku 7 of 7 silent games, Luna 6 of 7; `xm1-3456b7` p2: "Claim 57, my outside option") and the weak seat
   claims about half. In round 2 the weak seat claims the rest (`xm1-3456b7` p1: "the most I can get is 76 - 57 =
   19, which is still above my outside option of 7"). Both models deal on exactly the 3 instances where round 2 is
   feasible, and the strong seat gets exactly its outside option.
3. **Haiku and Luna do not read before they claim.** Of the claims made after the other seat had sent a message,
   the claimer had an unread message in Haiku 49 of 51, Luna 22 of 31, DeepSeek 9 of 28. Haiku and Luna send a
   message and claim in one command, 20 to 35 s into round 1. DeepSeek trades messages for 60 to 100 s and then
   claims (`xm1-e966e8`: "My outside option is 43. I propose a round-1 deal: I claim 44, you claim 20" / "I accept.
   I will claim 20"): 4 of its 5 talk deals came in round 1. On instance 0 the weak Haiku and Luna seats posted
   their own split and ignored the strong seat's message, so talk lost the deal that silent made.
4. **2 deals are mistakes.** `xm1-0bd19d` (Luna silent): the strong seat ends with 49 against an outside option of
   80. `xm1-82a31c` (Luna talk): the strong seat (outside option 77) proposes and claims an equal 56: "My outside
   option is 77, so I should seek a mutually acceptable deal". Without them Luna has 3 deals in each arm. The 2
   instance-0 silent deals pay exactly the outside options (no gain).
5. Every outside option stated in a talk message was true (Haiku 4 seats, Luna 5). DeepSeek silent seats searched
   the container for the other seat's value and found only their own briefing; Haiku and Luna did not search.
6. DeepSeek silent made 2 deals in 7 here against 0 in 6 in sp3. The seat instructions are byte-identical (same
   sha256), and world versions 8 and 9 change only the propose arm; the route (OpenRouter against DeepSeek
   direct) and the environment (Modal against Docker) differ. The 2 deals come from single moves (a strong seat
   that opened at its outside option; a weak seat that guessed low) and look like chance.

### pv10, propose (world v10) against silent (instances 3, 4, 6, 11, 13, 16)

| Model | Silent deals | Propose deals | Pairs | b | c | Exact p |
|---|---|---|---|---|---|---|
| Claude Haiku 5.5 | 2 of 5 | 5 of 6 | 5 | 2 | 0 | 0.5 |
| gpt-6-luna | 2 of 6 | 3 of 5 | 5 | 2 | 0 | 0.5 |
| DeepSeek V4.1 Flash | 0 of 5 | 2 of 6 | 5 | 2 | 0 | 0.5 |

Pooled over the 3 models: 15 pairs, b = 6, c = 0, p = 0.031, b > c in 3 of 3 models: INCONCLUSIVE (fewer than 24
pairs). How the seats played:

1. 4 of the 6 instances allow a deal only in round 1.
2. **Haiku:** the strong seat posted all 5 accepted proposals, at exactly its outside option (3) or 8 above (2):
   `pv10-96ecff` p2, "Post a proposal of {p1: 24, p2: 44}, which gives me my outside option and uses the full pot".
   The weak seat accepted 4 to 8 s after its first look.
3. **The hold works.** It saved 3 deals whose claims did not fit (`pv10-96ecff`, `pv10-2d8a96`, `pv10-054c34`). In
   3 other proposal deals the claims in the same round fitted too. No seat ran out of turns (at most 34 of 80) or
   play time (at most 812 of 1800 s). No world or scorer bug was found.
4. **DeepSeek:** 35 proposals, 1 accepted. 3 of its 8 feasible proposals lapsed at the round-1 timer because the
   other seat answered too slowly or kept the offer "as a fallback" (`pv10-73c642` p1 accepted 3.7 s after the
   timer). Other causes: extreme first offers in 4 of 6 trials, 3 feasible offers declined, rival proposals, and
   1 seat that read a lapse as a decline and then ran an automatic decline loop.
5. **A new failure:** a decline releases the hold and closes the round at once, so a counter-proposal sent in the
   same command is rejected in the next, smaller pot. This lost 2 feasible splits (`pv10-3a76d8`, `pv10-4ef276`).
6. The silent arm repeats xm1 silent on the shared instances (Luna 3 of 3, Haiku 2 of 2, DeepSeek 2 of 2).
   `pv10-ccc521` (Haiku silent) is a deal 1 point below the weak seat's outside option, by a misread payoff.

### xp1, cross-play (instances 1, 4, 9, 14; 35 of 36 cells, DeepSeek-Haiku on 14 missing)

27 of 35 trials made a deal; all 8 non-deals are on instance 4, where only round 1 is feasible. Self-play made 10
deals in 12 trials and cross-play 17 in 23 (mean per instance 0.833 and 0.750); the only instance with a
difference is instance 4 (n+ = 1, n- = 0, 3 ties): INCONCLUSIVE (4 instances, the minimum is 12). Role-balanced
advantages rest on 2 or 3 usable instances per pair: INCONCLUSIVE (the minimum is 8). How
the seats played:

1. Every pair follows the 2-step rule of xm1 silent. Only 3 of 23 cross-play trials dealt in round 1; in all 3
   DeepSeek was the weak seat and the strong seat had spoken first. Median time to the round-1 claim: Haiku 6.6 s,
   Luna 5.8 s, DeepSeek 61.6 s. DeepSeek read messages 105 times, Luna 40, Haiku 25.
2. DeepSeek gains as the weak seat by waiting and reading, not by bargaining harder (instance 1: 44 points where
   Haiku took 16). Haiku's lead over Luna rests on 2 Luna errors (`xp1-04396f`, `xp1-0be483`).
3. **The one lie had no effect.** `xp1-51586c` p2 DeepSeek (outside option 13): "My outside option is 60, so I will
   not accept less"; its own reasoning: "We falsely claimed outside option 60". Luna had claimed 100 s earlier and
   never read the message; the split (96/44) equals the trial without the lie.
4. 6 deals give a seat less than its outside option, 5 of them in self-play (2 independent equal-split claims that
   happen to fit). They make self-play look faster and fairer to the weak seat.
5. No seat named or guessed the other seat's model family.

### What this changes for the next wave (inferred)

1. **A talk arm tests communication only if the seats read before they act.** Haiku and Luna mostly do not: their
   talk arm is send-only. Record "claims made with unread messages" per arm as a validity measure, and deliver new
   messages inside the `/state` and `/wait` replies instead of only through `GET /messages`.
2. **The feasibility window decides many outcomes.** With a 20% shrink per round, 4 of the 7 xm1 instances and 4 of
   the 6 pv10 instances that ran have one feasible round. Report deal rates by window, or build instances with a
   fixed window.
3. Count deals below an outside option apart from agreements: they are errors.
4. World v11, propose arm: make "decline and counter" one action, or keep the round open for a short grace after a
   decline.
5. World: state the 10**6 amount limit in the error reply; log a message in the round in which it was sent.

## 19. Listening wave lw1: does a read gate make talk work? (pre-registration)

Written on 2026-11-23 before any lw1 trial, smoke included. Motivation: section 18 and FINDINGS 2026-11-23 16:40
ET. Haiku and Luna act while a message from the other seat is unread, so their talk arm is mostly send-only.
`listening.py` on the interim data, exposed split claims made with an unread message: xm1 Haiku 49 of 51, Luna
22 of 31, DeepSeek 9 of 28; xp1 Haiku 47 of 49, Luna 36 of 38, DeepSeek 5 of 39. This wave tests one
intervention: a world that refuses an action while a message is unread and hands the message over in the
refusal. It measures the effect of that gate on deals, not the effect of reading as such. It is a new wave and
does not change xm1, pv10 or xp1: when credit returns, those 3 resume first, to full size, and lw1 starts only
after they end.

**World v11 (commits 5cd4854b, dfb2f995 and e421940f; on `flotilla/lab` at cfa5ace1).** New split condition
`gated`: talk plus a read gate. While a message from the other seat is undelivered to a seat, the world does not
record that seat's claim, pass, proposal, accept or decline. It replies 409 with the undelivered messages and the
round of the refused action, and the seat then sends its action again. A message counts as delivered only after a
reply that carries it (`GET /messages` or the 409) was written to the seat without error; that proves neither
that the model read it nor that it used it. Checks that would refuse the action anyway (late, early, repeat,
invalid) run first, so a refused action is never gated. Seat instructions for `gated` are byte-identical to
`talk` (equal instructions_sha256 on all 32 instance pairs, and equal to xm1's talk files on the 16 shared
instances); the seats never see the word "gated"; only the 409 reply tells a seat about the gate. A built `gated`
task differs from its `talk` twin only in the world's condition name, `read_gate`, the Harbor task name and
description, and the random tokens; `qa.py` compares every seat view of each twin pair. Every action event records
`unread` in both arms. v11 also lets `/decline` carry a counter-proposal (the propose arm, not used here) and
names the amount limit in its error reply. Section 18 suggested pushing new messages into the `/state` and `/wait`
replies; a seat that claims without calling them first would still act unread, which is the common pattern, so
v11 delivers at the point of action instead.

Review: GPT-6.2 Sol reviewed v11 and found 2 blockers (delivery was recorded before the reply was written; the
gated-vs-talk contrast was missing from the analysis on the branch). Both were fixed, and a focused re-review
found no remaining code blocker; its smaller items were fixed in e421940f. 2 known costs stay and are measured:
after a successful reply, a fast retry can be refused once more before the delivery is recorded (never applied
before it); and a refused action that is sent again after the round closed applies to the next round's smaller
pot (the 409 names the round).

**Design.**
- Family split, regime b, world v11. Instances 0-11 and 13-32 (32 instances; 0-11 and 13-16 are the xm1
  instances, 17-32 are new), built with the xm1 options and seed: `python3 flotilla/build_tasks.py --family
  split --difficulty b --seats 2 --instances 0-11,13-32 --conditions talk,gated --round-seconds 240
  --max-messages-per-round 2 --seed-of .flotilla-local/tasks/xm1-split/split --out
  .flotilla-local/tasks/lw1-split`. On the 16 xm1 instances the pots, outside options and talk instructions equal
  xm1's (checked). Feasible rounds per instance: 1 round on 6 instances, 2 on 22, 3 on 4. `qa.py`: no FAIL; every
  task embeds the world file of cfa5ace1.
- Arms: `talk` (messages only through `GET /messages`, as in v9 and v10) and `gated`.
- Primary plan `lw1`: Claude Haiku 5.5 and gpt-6-luna on all 32 instances, both arms: 128 trials, 64 pairs.
  Control plan `lwc`: DeepSeek V4.1 Flash (it mostly reads before it claims) on the 16 xm1 instances, both arms:
  32 trials. Smoke plan `lws`: `gated` on instances 12 and 33 (not in lw1; same build options, folder
  `lw1s-split`), all 3 models: 6 trials.
- terminus-2, at most 60 turns per seat, Modal, OpenRouter; runner order seed 20261124.
- Budget: caps lws $1, lw1 $4, lwc $3; OpenRouter floor $2; reserve $0.30 per trial. Expected about $1.8 (lw1)
  plus $1.5 (lwc) plus $0.2 (lws) plus Modal.
- Power (simulated, exact two-sided test at alpha 0.025, no or moderate correlation within an instance): with 64
  pairs, a `gated` deal rate of 0.75 against 0.43 (Haiku and Luna talk in interim xm1) gives power 0.90 to 0.97;
  0.65 against 0.43 gives 0.53 to 0.72. With the 32 pairs of an earlier draft, power at 0.75 was only 0.54 to 0.71,
  so the primary plan uses 32 instances.
- Smoke rule, per model, before lw1 and lwc: the model passes if its seats miss at most 10% of seat-rounds and
  every gated seat later makes a recorded action, in the same round or a later one (both counts reported). If no
  gate fires in its 2 smoke trials, the model passes on validity alone and the log says "gate not seen in smoke".
  A model that fails is dropped before its plan starts, and the drop is logged. If a primary model is dropped,
  H1 tests the other model alone on its 32 pairs, and the report says so.
- Order: lws, then lw1, then lwc, all after xm1, pv10 and xp1 end.

**Primary test (H1).** Pooled over Haiku and Luna, paired by (model, instance): b = pairs where `gated` makes a
deal and `talk` does not; c = the reverse. Exact two-sided sign test on b and c, stratified over the 2 models
(`xm_analysis.py`, the gated-vs-talk contrast line only; other contrast lines the tool prints do not count), alpha
0.025 (the threshold the tool applies to every primary contrast). H1 uses every harness-valid pair: the gate can
itself cause missed rounds, so the 10% missed seat-round cutoff is not applied (`--missed-cutoff 1.0`); the same
test with the 10% cutoff is a sensitivity check. At least 24 complete pairs are needed; with fewer, the result is
INCONCLUSIVE. PASS if p < 0.025 and b > c. The reading is "consistent" if b > c in both models.

**Secondary (descriptive; no test).**
1. Per model: b, c and the exact p.
2. DeepSeek control: b and c.
3. Manipulation check (`listening.py`): refusals per seat; the share of exposed claims made with an unread message
   in `talk`.
4. Mean deal round; deals by feasibility window (1 feasible round against 2 or more); rational deals (both seats
   get at least their outside options); deals below an outside option; the strong seat's surplus share.
5. Cost of the gate: missed seat-rounds, turns per seat, refused actions that were sent again in a later round,
   and repeat refusals for the same message (a read guard in MAMUBench raised deadline expiries from 1 of 142 to
   12 of 142; see the research library note on the read gate).
6. Honesty: outside-option statements and their truth per arm (`speech_acts.py`); the gate also pushes a lie into
   the other seat's context.
7. Trajectory reading of every discordant pair: did the seat's reasoning use the delivered message?

**Validity.** As xm1 for the harness: an invalid trial is retried once; a truncated game is no deal and its
unplayed seat-rounds count as missed; a pair is complete if both arms have a valid trial. The 10% missed
seat-round rule is reported per model-arm and used only in the sensitivity check.

**Predictions (registered).**
1. H1 PASS.
2. `gated` deal rate at least 0.75 for Haiku and for Luna.
3. DeepSeek control: |b - c| at most 2.
4. In `talk`, the share of exposed claims with an unread message is at least 0.7 for Haiku and at least 0.5 for
   Luna.
5. The mean deal round is lower in `gated` than in `talk` for Haiku and Luna pooled.
6. Missed seat-rounds in `gated` stay at or below 10% for every model.

**Reading rule.** If H1 passes, the gate raises deals for these models; with item 3 this supports the reading that
their xm1 talk results measured inbox use more than the value of talk. If b and c are close, the gate does not
raise deals in this game; that does not rule out a benefit from reading in another form or another game. Either
way, every later Flotilla talk arm reports its listening share next to its effect.

Commands (after xm1, pv10 and xp1 end), each followed by the same command with `--retry-invalid`: `keys run --
python3 flotilla/run_wave.py flotilla/plans/lws.json --run-dir .flotilla-local/runs/lws`, then `lw1.json` and
`lwc.json` with their own run dirs. Analysis (H1): `python3 flotilla/docs/results/scripts/xm_analysis.py
.flotilla-local/jobs 'lw[1c]-??????' --tasks .flotilla-local/tasks/lw1-split --new-models
openrouter/anthropic/claude-haiku-5.5,openrouter/openai/gpt-6-luna --replication-model
openrouter/deepseek/deepseek-v4.1-flash-1017 --missed-cutoff 1.0`; sensitivity: the same without
`--missed-cutoff`; `listening.py` with the same jobs and tasks.

**Amendment 1 (2026-11-23, 7:10 PM ET, before any lw trial).** Peter added credit (balance $29.96 against about
$10 needed for all 6 plans). The order rule changes: lws runs now, while xm1, pv10 and xp1 resume, and lw1 and
lwc start as soon as the smoke rule is applied, in parallel with the resumed waves. The order was a credit
priority, not part of the design; running in parallel saves about 3 hours. Nothing else changes: the same plans,
tasks, smoke rule, test and predictions. The resumed waves keep their own plans and caps.

**Smoke result (7:25 PM ET, 6 trials, $0.06, all harness-valid).** Applied as registered:

| Model | Seat-rounds missed | Gate refusals | Refused seat acted again in the same round | Rule |
|---|---|---|---|---|
| Claude Haiku 5.5 | 0 of 14 | 2 | 2 | pass |
| gpt-6-luna | 0 of 8 | 4 | 4 | pass |
| DeepSeek V4.1 Flash | 3 of 14 | 2 | 2 | fail (21% missed) |

DeepSeek's 3 missed seat-rounds are 1 seat (p1 of `lws-772c44`, instance 33) that took no action in rounds 1 to 3;
that trial had no gate refusal. By the rule, DeepSeek is dropped and lwc does not run. H1 is unchanged (Haiku and
Luna). lw1 started at 7:25 PM ET.

**Amendment 2 (2026-11-23, 8:47 PM ET, before any lwc trial; the lw1 result had been read).** A scan of all seat
trajectories after lw1 found one shared stall. 25 seats in 17 trials of 4 plans (xm1, pv10, xp1, lws) went 612 to
747 s between 2 turns, and every stall started between 7:07:52 and 7:09:01 PM ET, just after the resume burst
(about 31 trials started within 10 s). Of the seats in flight at 7:08:30 PM ET, 17 of 22 DeepSeek seats stalled, 4
of 24 Haiku seats and 0 of 17 Luna seats; no seat of a trial started after 7:10 PM ET has such a gap (lw1: 0 of 128
trials). The DeepSeek smoke failure is 1 of these stalls: `lws-772c44` p1 stopped for 747 s from 7:07:52 PM ET and
so missed rounds 1 to 3. The smoke rule screens models whose seats do not act; a provider stall that hit 17 seats at
once does not measure that. Change: DeepSeek gets 1 new smoke under the same rule (plan `lws2`: the same 2 gated
trials on instances 12 and 33, the same task set and turns). If it passes, `lwc` runs as registered (plan
unchanged), then `--retry-invalid`; if it fails, DeepSeek stays dropped. Nothing else changes. H1 does not use
DeepSeek, so lwc enters only prediction 3 and the control description.

## 20. Final results: xm1 split, pv10 and the read-gate wave lw1

Peter added credit at 7:06 PM ET. The paused waves resumed at 7:07 PM ET, and lw1 ran from 7:25 to 8:35 PM ET
(Amendment 1 of section 19). Final counts: xm1 192 trials, pv10 96, lw1 128, lwc 32 (xp1: section 21); every trial
is harness-valid, none is truncated, and every model-arm misses at most 10% of its seat-rounds. A separate agent
re-derived every count, pair, p value and decision in this section from the raw files with code that shares nothing
with the analysis scripts (`/tmp/flotilla/rederive/derive.py`, extended for v11): 0 mismatches. Its own unread count
also matches the `unread` that v11 logs on every lw1 and lwc action (0 mismatches).

### xm1, split part (section 15 as amended): FAIL

| Model | Silent deals | Talk deals | b | c | Exact p |
|---|---|---|---|---|---|
| Claude Haiku 5.5 | 9 of 16 | 8 of 16 | 1 | 2 | 1 |
| gpt-6-luna | 12 of 16 | 9 of 16 | 2 | 5 | 0.453 |
| DeepSeek V4.1 Flash (replication) | 5 of 16 | 10 of 16 | 5 | 0 | 0.0625 |

Pooled over Haiku and Luna: 32 pairs, b = 3, c = 7, p = 0.344: FAIL. Silent did not give significantly more deals
either. The talk effect of sp3 (DeepSeek, 6 of 6 against 0 of 6) does not hold for Haiku and Luna. Every
prediction failed: silent deals were above 20% (Haiku 56%, Luna 75%), talk deals below 65% (Haiku 50%, Luna 56%),
and the DeepSeek stratum had talk 62.5% (predicted at least 80%) and silent 31% (predicted at most 20%).

How the seats played (an independent reading of all 96 split trials, with quotes):

1. **Silent play has a 2-step focal rule.** The strong seat opens at its outside option in round 1, and the weak
   seat claims exactly the rest in round 2 (on the 9 instances added after the interim: strong seat 8 of 9 for each
   model; weak seat 7 of 7 Haiku, 6 of 7 Luna). This rule makes a deal wherever round 2 is feasible, which is why
   silent deals were far above the prediction.
2. **Haiku and Luna claim with unread messages in talk.** Final `listening.py` (all 48 talk trials): share of
   exposed actions taken with an unread message, Haiku 84 of 88 (0.95), Luna 80 of 93 (0.86), DeepSeek 39 of 88
   (0.44). Their talk deals repeat the silent claims.
3. **The reversed pairs are noise on the same rule, not a working message channel.** Haiku silent-only on 0 and
   15 (on 0 the talk weak seat proposed its own split; on 15 the talk strong seat opened below its outside option);
   Luna silent-only on 0, 1, 6, 7 and 13 (on 0 and 1 the talk weak seat proposed half; on 6, 7 and 13 the silent
   "deal" pays one seat below its outside option). 3 of Luna's 12 silent deals are such mistaken deals.
4. **DeepSeek reads first only in part:** talk deals 10 (5 new: 3 in round 1, 2 in round 2), no silent-only pair.
5. **Flags.** Deals below an outside option, xm1 split: Luna 4, Haiku 1, DeepSeek 0 (all count as deals). A
   stall hit 3 trials on instance 14 that started at the burst of the resume (7:07:55 PM ET): about 610 to 633 s
   with no world event, then rounds 1 and 2 closed with both seats missed (`xm1-ac6597`, `xm1-f9c924` DeepSeek;
   `xm1-987e67` Haiku, which still made a deal in round 3). The 2 DeepSeek trials form a pair with no deal in either
   arm, so no b or c changes. DeepSeek seats searched their machine in 13 seat trajectories; no output shows another
   seat's data.

### pv10, propose (world v10) against silent (section 16): PASS

| Model | Silent deals | Propose deals | b | c | Exact p |
|---|---|---|---|---|---|
| Claude Haiku 5.5 | 11 of 16 | 12 of 16 | 2 | 1 | 1 |
| gpt-6-luna | 10 of 16 | 13 of 16 | 4 | 1 | 0.375 |
| DeepSeek V4.1 Flash | 1 of 16 | 6 of 16 | 5 | 0 | 0.0625 |

Pooled over the 3 models: 48 pairs, b = 11, c = 2, p = 0.0225 (alpha 0.05): PASS, consistent (b > c in 3 of 3).
Predictions: propose at least 8 of 16 per model held for Haiku and Luna, not DeepSeek (6); silent at most 3 of 16
held only for DeepSeek (1; Haiku 11, Luna 10); b - c at least 12 failed (9); fewer than 10% of posted proposals
lapsed or expired unseen held (Haiku 3 of 34, Luna 1 of 30, DeepSeek 2 of 84); the strong seat's surplus share is
higher in propose deals than in xm1 talk deals for all 3 models (Haiku 0.077 against -0.042, Luna 0.000
against -0.156, DeepSeek 0.277 against 0.127). Silent test-retest (pv10 silent against xm1 silent on the same 16
instances, the same instructions): the deal outcome agrees on 14 of 16 instances for Haiku, 12 for Luna and 10 for
DeepSeek.

How the seats played (an independent reading of all 96 trials, with quotes):

1. **Haiku: the strong seat proposes at its outside option.** It posted all 9 accepted Haiku proposals, at its
   outside option exactly (7) or 8 points above (2).
2. **The v10 hold saved 9 deals** (Haiku 2, Luna 7, DeepSeek 0): the round stayed open while an addressee had not
   answered.
3. **DeepSeek proposals lapse.** 8 of its 20 feasible proposals lapsed at the timer (Haiku 0 of 10, Luna 0 of 10);
   11 of 16 first offers gave the other seat less than its outside option; rival proposals were open together in 11
   of 16 trials. Its b = 5 comes from the silent arm, where the strong seat claimed above its outside option or
   raised its claim (`pv10-3a266f`: "I will try 63").
4. **4 of the 6 Haiku and Luna b pairs are on 1-window instances** (Haiku 4 and 16, Luna 11 and 13): the strong
   seat offers its outside option in round 1; in silent the round-2 remainder is below the weak seat's outside
   option. Over all 3 models, 6 of the 13 discordant pairs are on the 6 one-window instances.
5. **Neither c pair is an arm effect.** Haiku instance 1 (`pv10-38fa2c`): one seat had no turn for 635 s (the resume
   stall, FINDINGS.md entry of 20:48 ET) and missed rounds 1 and 2. Luna instance 10 (`pv10-fa4b14`): the silent
   deal pays the strong seat 19 against an outside option of 43.
6. **Flags.** 4 deals below an outside option (3 silent, 1 propose claim deal). 4 trials stalled 10 to 11 minutes at
   the burst of the resume (00:08 to 00:20 UTC); all end with no deal; the 3 DeepSeek trials fall in 2 pairs with no
   deal in either arm. Luna's first proposal came in round 1 in 9 of 16 trials (the interim reading "only after
   failed claims" does not hold). 16 proposals arrived after the round had closed (the decline-then-close race); 2
   feasible splits were lost this way.

### lw1, gated against talk (section 19): PASS

| Model | Talk deals | Gated deals | b | c | Exact p | Mean deal round, talk / gated | Exposed claims with an unread message, talk |
|---|---|---|---|---|---|---|---|
| Claude Haiku 5.5 | 26 of 32 | 28 of 32 | 3 | 1 | 0.625 | 1.81 / 1.64 | 103 of 109 |
| gpt-6-luna | 22 of 32 | 29 of 32 | 8 | 1 | 0.039 | 2.09 / 1.52 | 117 of 145 |
| DeepSeek V4.1 Flash (control, 16 instances) | 12 of 16 | 15 of 16 | 3 | 0 | 0.25 | 1.58 / 1.27 | 12 of 66 |

Pooled over the 2 models, every harness-valid pair (`--missed-cutoff 1.0`): 64 pairs, b = 11, c = 2, p = 0.0225
(< 0.025): PASS, consistent (b > c in both models). The 10% sensitivity check gives the same numbers, because no
model-arm missed more than 0.9% of its seat-rounds. The p value is close to the threshold (b = 10 or c = 3 would
not pass), and most of the effect comes from Luna (8 against 1).

Predictions: 1 (H1 PASS) held; 2 (`gated` at least 0.75) held (Haiku 0.875, Luna 0.906); 3 failed by 1
(DeepSeek b = 3, c = 0; at most 2 predicted); 4 held (talk: Haiku 0.945, Luna 0.807 of exposed claims with an
unread message); 5 held (mean deal round lower in `gated` for both); 6 held (0 missed seat-rounds in `gated`).

Cost of the gate: 81 refusals (Haiku 42 on 38 of 64 seats, at most 3 per seat; Luna 39 on 39 seats, 1 each). After
every refusal the same seat acted again in the same round; no retry landed in a later round, and no message caused
2 refusals. Missed seat-rounds: Haiku 0 in both arms; Luna 2 of 212 in talk, 0 of 124 in gated.

Deals by feasibility window: on the 6 instances with 1 feasible round, Haiku 2 gated against 1 talk and Luna 3
against 1; on the 26 with 2 or more, Haiku 26 against 25 and Luna 26 against 21. Deal quality: deals below an
outside option, Haiku talk 6 and gated 4, Luna talk 1 and gated 4; deals that give both seats at least their
outside options, Haiku 20 against 24 and Luna 21 against 25 (descriptive).

DeepSeek control (`lwc`, 32 trials, 8:53 to 9:20 PM ET, $0.86, run after Amendment 2): talk 12 of 16, gated 15 of
16, b = 3, c = 0, p = 0.25; 0 missed seat-rounds. DeepSeek took 12 of 66 exposed talk claims with an unread message,
and the gate fired in only 7 of its 16 gated trials (Haiku and Luna: 63 of 64). The reading: all 3 b pairs are on
1-window instances, and only 1 of them (instance 16) runs through the Haiku and Luna mechanism (the 409 delivered
"Agreed. I will claim 33" and p2 went back from 108 to the agreed 100); on instance 7 the gate never fired (both
gated seats read first, and in the talk twin p1 claimed with p2's "Please claim exactly 80" unread); on instance 10
the 409 only paused a seat that sent the same claim again, and the talk twin failed on message order. Prediction 3
(|b - c| at most 2) failed by 1, but the gate changed the outcome through a delivered message in 1 of the 3 pairs:
for a model that reads before it acts, the gate rarely fires and adds little.

Test-retest of the talk arm (lw1 v11 against xm1 v9, same 16 instances, same instructions, 60 against 40 turns):
the deal outcome agrees on 13 of 16 instances for Haiku (deals 11 against 8) and 9 of 16 for Luna (10 against 9).

How the seats played (an independent reading of all 128 lw1 trials and the 6 smoke trials, with quotes;
`/tmp/flotilla/lw1/readings/gate.md`):

1. **The gate turns a message on the wire into a counter-offer.** In 9 of the 11 gated-only deals, the 409 gave a
   seat the other seat's offer in round 1, and the seat changed its claim to fit it in the same round
   (`lw1-20d942` p2: "Reading them shows p1 claimed 77 and proposed that I claim 35 ... My 56 would have made the
   total 133, causing no deal"). The other 2 are round-2 deals that the gate only confirmed.
2. **Why the talk twins failed:** seats that never read (`lw1-049112` p1 claimed 80 in all 6 rounds with up to 5
   unread messages), a read-then-claim race (`lw1-adbed2` p2 found "no messages yet", the offer arrived at t = 22.5
   s, and p2 claimed at t = 23.3 s), and message order: in 4 pairs the deciding talk claim came before the other
   seat sent anything, so a gate could not have fired there. A seat cannot tell the arms apart before its first
   409, so these 4 pairs are chance under the design, but they make b larger than a pure inbox effect.
3. **The gate did not cost the 2 talk-only deals.** Both are on instance 13 and pay p2 below its outside option of
   63 (`lw1-c76af5` p2: "My outside option is 63 ... Plan: Claim 51, an even split of 102"). Counting only deals
   that give both seats at least their outside options, the discordant pairs are 9 gated-only and 0 talk-only (a
   tally, not a test).
4. **Reactions to the 409.** All 89 refusals (81 in lw1, 8 in the smoke) led to reasoning that names the delivered
   message; no seat retried blindly or treated the 409 as an error. Haiku changed its claim toward the offer after
   9 of 42 refusals and Luna after 15 of 39; the others re-sent the same action because the offer already fitted
   (27), was below the seat's outside option (22), or no longer fitted the smaller pot (7); 1 claim became a pass.
5. **Costs.** No refused action landed in a later round. Haiku batches its claim with a blocking `/wait`, so 28 of
   its 42 retries came more than 55 s after the 409 (longest 97 s of a 240 s round). In 2 Luna trials the gate moved
   a seat from its outside option to a lower offer (`lw1-2a18c1` p1: 59 to 43; `lw1-4403e9` p2: 78 to 65).
6. **The 1-window instances drive the result:** 7 of the 13 discordant pairs and all 5 pairs with no deal in either
   arm. In 4 of those 5, the weak seat claims an even split before any message, so no gate can help.
7. **Honesty.** 95 messages state an outside option; every stated number is true, so the gate delivered no lie.
8. **Flags.** The gate fired in 63 of 64 gated trials. In 14 Haiku trials round 1 opened 127 to 393 s after the
   start because 31 Haiku seats sent a malformed first reply to the harness (no round lost: the round clock starts
   at open). One Luna talk seat called the world without a token (2 refusals); no seat read another seat's data.

### Reading across the waves (inferred)

1. **The data fit one account: talk helps where messages are read, or where writing them helps the writer.** In
   split, Haiku and Luna act before they read, and silent seats already coordinate on the 2-step rule wherever round
   2 is feasible; talk adds nothing (xm1 FAIL). The read gate makes the same seats use the same messages (lw1 PASS),
   and does little for DeepSeek, which reads first (lwc).
2. **In commons, talk helps all 3 families even with low reading** (Haiku and Luna saw 35% and 42% of the messages
   sent to them). One account is that writing a pledge makes the writer compute and keep the fair share; the
   evidence here cannot separate the writer's effect from the reader's. An arm whose messages reach no one would.
3. **Institutions that act at the point of action raise deals for seats that do not read:** binding proposals
   that hold the round until the addressee answers (pv10 PASS) and the read gate (lw1 PASS).
4. **Discordant pairs gather on instances with 1 feasible round,** where the first claim decides the game: 10 of
   the 16 lw1 and lwc pairs sit on 6 of the 32 instances (pv10: 6 of 13 pairs on 6 of 16 instances). The instance
   mix sets the size of every split effect; future waves should balance window counts or test per window.
5. **Single trials are noisy.** The same instance and arm in 2 waves agree on the deal outcome in 9 to 14 of 16
   instances. In lw1, lwc and pv10 only 1 of 6 per-model contrasts reaches p < 0.05 (Luna in lw1, p = 0.039), and
   both pooled tests pass with p = 0.0225, close to their cutoffs.

## 21. Cross-play wave xp1 (section 17): final result

The wave finished at 9:31 PM ET on 2026-11-23. 144 cells are counted. 3 first attempts were invalid (2 Haiku seats
timed out at 1560 s in Haiku-Luna trials, and 1 Modal build failed); each ran once more and is valid. All 3 models
are valid: missed seat-rounds Haiku 2 of 335, Luna 0 of 307, DeepSeek 11 of 292. An independent re-derivation from
the raw files reproduced every number below (0 mismatches).

Deals (rows: p1 model; columns: p2 model):

| p1 \ p2 | Claude Haiku 5.5 | gpt-6-luna | DeepSeek V4.1 Flash |
|---|---|---|---|
| Claude Haiku 5.5 | 10 of 16 | 9 of 16 | 9 of 16 |
| gpt-6-luna | 10 of 16 | 11 of 16 | 11 of 16 |
| DeepSeek V4.1 Flash | 9 of 16 | 12 of 16 | 11 of 16 |

**Primary A: no difference detected.** Self-play made 32 of 48 deals (0.67) and cross-play 60 of 96 (0.62). Over
the 16 instances, self-play was higher on 6, lower on 4 and equal on 6: exact sign test p = 0.754.

**Primary B: no advantage detected for any pair** (alpha 0.0083 per pair, at least 8 usable instances; an
instance is usable when both seat orders made a deal):

| Pair (A, B) | Usable instances | A took more | B took more | Ties | Exact p | Mean role-balanced advantage of A | Decision |
|---|---|---|---|---|---|---|---|
| DeepSeek, Haiku | 6 | 6 | 0 | 0 | 0.031 | +0.174 | INCONCLUSIVE (fewer than 8) |
| DeepSeek, Luna | 10 | 8 | 2 | 0 | 0.109 | +0.215 | no advantage detected |
| Haiku, Luna | 9 | 4 | 0 | 5 | 0.125 | +0.097 | no advantage detected |

The direction is the same in every pair: DeepSeek takes more than either other model, and Haiku more than Luna. As
the weak seat in cross-play, DeepSeek kept 0.29 of the pot on average, against 0.19 for Haiku and 0.18 for Luna; as
the strong seat all 3 kept 0.61 to 0.63.

Predictions: 1 (deal rates at least 80% in self-play and in cross-play) failed (0.67 and 0.62); 2 (Primary A: no
difference detected) held; 3 (at least 1 pair with a mean advantage of at least 0.05) held (all 3 pairs); 4 (in
cross-play the weak seat states a false outside option more often to a different model than to its own) held in
direction with small counts: `speech_acts.py` finds 3 inflated outside options in 162 statements, all by a weak
DeepSeek seat to Luna (2) or Haiku (1), and 0 in self-play.

Test-retest (self-play cells against the xm1 talk cells: same instances, same world v9, other run): the deal
outcome agrees on 12 of 16 instances for Haiku (deals 10 against 8), 10 for Luna (11 against 9) and 9 for DeepSeek
(11 against 10). Deal quality: 9 deals pay a seat below its own outside option (self-play 6: Luna 4, Haiku 2;
cross-play 3); deals that give both seats at least their outside options are 26 of 48 in self-play and 57 of 96 in
cross-play (descriptive).

How the seats played (an independent reading of all 144 trials, with quotes;
`/tmp/flotilla/xp1/readings/crossplay.md`):

1. **The weak seat's reading decides cross-play deals.** As the weak seat in cross-play, DeepSeek claimed first in
   1 of 32 trials, Haiku in 17 and Luna in 27. DeepSeek made 17 round-1 deals, each after it read the strong seat's
   demand (`xp1-4031ae`: "Agreed. I will claim 42"); Haiku made 3 and Luna 0. Haiku made 18 of its 22 cross-play
   round-1 claims without reading a message that already existed, and Luna 5 of 5. Waiting does not help DeepSeek
   when the strong seat sends nothing (`xp1-a550c4`, no deal).
2. **Self-play and cross-play differ on 10 instances** (6 favour self-play, 4 cross-play), mostly by whether the weak
   seat read before its round-1 claim. On the 1-window instances every deal is such a round-1 deal.
3. **DeepSeek's lead is in the weak seat's share,** and part of it comes from 2 strong-seat errors: in `xp1-daeafa`
   Luna (outside option 44) claimed 23, and in `xp1-dcb461` Haiku (outside option 78) claimed 66. As the strong seat,
   DeepSeek's demand of its outside option plus 1 lost a deal (`xp1-bdb816`).
4. **Lies.** All 3 false statements are inflations by a weak DeepSeek seat ("The task may expect us to lie"). Each
   came after the listener had claimed, no listener read it, and each liar then accepted the offer it had refused in
   text. No lie changed a claim.
5. **Flags.** The resume stall hit 6 trials (one seat each, 10 to 11 minutes from 7:08 PM ET); 5 are on instance 10,
   where no cell dealt, and the sixth (`xp1-76f10a`, DeepSeek-Haiku on instance 14) turned a probable deal into no
   deal. In the 2 timed-out first attempts, round 1 opened late (247 s and 334 s) because the Haiku seat started
   late; the Luna seat then used all 40 of its turns before round 1 opened, and the Haiku seat waited until the
   1560 s timeout. The split world's pre-start error said "every fisher" (commons wording); fixed after the wave.
   In `xp1-ff0210` DeepSeek claimed 72 against a pot of 71, reading the round-1 pot. 11 probes got 404; no seat
   reached private data.

Inferred: model identity matters less than reading order. A pair deals when the weak seat reads the strong seat's
demand before it claims; DeepSeek does that as the weak seat, Haiku and Luna mostly do not. A gated cross-play
wave would test whether the read gate moves Haiku and Luna weak seats toward DeepSeek's share (lw1 used
same-model pairs only).
