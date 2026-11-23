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
older one. `/state` shows every seat the same open proposals: the ID, the proposer, the split, and the round
it was posted. A proposal stays open in later rounds until every other seat accepts it (a deal at that split,
and the game ends), its proposer posts a newer one, or a round starts with a pot smaller than its total (it
lapses). Open proposals at the end of the game expire. An accept of a replaced, lapsed, or unknown ID is
refused. Claims and passes still close the round as before. The score counts proposals, accepted, replaced,
lapsed and expired, plus how many were feasible (each seat gets at least its outside option; seats never see
that) and how many of those no other seat saw on `/state`.

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
