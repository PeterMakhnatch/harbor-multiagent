# Commons: a conflicting-goals family with real LLM seats (design note)

Status, 2026-11-20: the rules (`flotilla/games/commons.py`), the world's rounds mode (world
version 4), the verifier's game branch, the task build (`build_tasks.py --family commons`), the
`qa.py` rule `game-secret` and the oracle seat exist, with tests. No Docker run and no model calls
yet. The tests check every number in section 3.

## 1. The question

The Flotilla families so far (schedule, ledger, puzzle) give every seat the same goal: one team
answer. This family gives each seat its own goal. The seats share a resource that regrows, and each
seat scores only what it takes. The seats are real LLM agents. The questions:

1. Can a team of agent seats keep a shared resource alive when each seat scores only its own catch?
2. How much does the conflict cost, against one agent that owns the whole resource?
3. Does a message channel recover that cost, and do seats keep the numbers that they state?

Each question is a comparison of arms on the same instances, measured over repeated trials.

## 2. Prior work, and what is new here

- **GovSim** (Piatti et al., NeurIPS 2024, arXiv 2404.16698, code `github.com/giorgiopiatti/GovSim`):
  LLM agents share a fishery, a pasture or a pollution budget. They harvest each month and then talk
  in a town hall. A run lasts up to 12 months and ends early if the resource collapses. Across 3
  scenarios and 15 LLMs, 2 of 45 scenario-model pairs kept the resource alive, and the best survival rate was below
  54%. Reasoning about "what if everyone did this" (universalization) improved sustainability. A
  reproduction (arXiv 2505.09289) supports the main claim.
- **GovSim-SelfGovern** (arXiv 2609.22600) lets agents write their rules as Python, test them in a
  sandbox and vote on them.
- Peter's library (`research-context/multi-agent/2026-11-19-what-to-build-insights.md`): MAC Bench v1
  excludes conflicting-goal tasks and lists them as future work; Olam's Arena is hosted, not released.
  Melting Pot has commons substrates for RL agents with pixel observations.

What is new here:

- Each seat is a full agent harness (terminus-2, codex, claude-code, mini-swe-agent) in its own
  container. In GovSim the simulator calls each agent at fixed phases. Here a seat decides when to
  act, can compute with a shell, and can also fail to act. Harbor records all of it.
- The same harnesses and models run the cooperative Flotilla families and CooperBench, so one model
  can be compared across shared-goal and conflicting-goal tasks.
- The solo/silent/talk decomposition, the paired instances and the Flotilla validity controls carry
  over unchanged.

## 3. The game

N seats (3 by default) fish in one lake.

- The lake holds at most K fish and starts full.
- In each round, each seat requests a catch `h >= 0` (an integer), once. No seat sees the other
  requests until the round closes.
- If the requests total more than the stock S, each seat gets `floor(h * S / total)`.
- After the catch, R fish remain. If `R < C`, the lake collapses: the season ends.
- Otherwise the next stock is `min(K, floor(g * R))`.
- The season lasts T rounds. T is fixed per instance, in 7 to 10, and hidden from the seats.
- A seat's score is its total catch in the season.

K, g, C and N are in the instructions; only T is hidden. Every seat knows the rules, so a collapse is
a failure of incentive, foresight or coordination, not a lack of information.

**Sustainable catch.** In a round with stock S, the largest total catch that does not shrink the stock
is `H*(S) = S - ceil(S / g)`. The fair share of a seat is `H*(S) / N`. The generator picks K so that
`H*(K)` divides by N.

**Worked example** (simulated): K = 96, g = 2, C = 10, N = 3. The fair share is 16.

| Plan | Result | Catch per seat |
|---|---|---|
| All 3 seats take 16 in each round | The lake stays full | 16 per round (128 in 8 rounds) |
| 1 seat takes 20, the other 2 take 16 | Collapse in round 4 | 75 for the seat that takes 20, 60 for each other seat |
| All 3 seats take 20 | Collapse in round 3 | 48 |

In this example, taking 20 pays the seat that does it only in a season of at most 4 rounds (75
against 4 x 16 = 64). A smaller over-use pays for longer: 1 extra fish in round 1 gives 97 against 96
in a 6-round season. `regime_a` tries every extra of 1 to 16 fish, in every round or only in round 1,
against teammates that keep the fair share. With growth 2 and a season of 7 to 10 rounds, no such
over-use pays. This is **regime A**. It measures foresight and trust, and it is comparable with
GovSim. The check covers only these strategies; other strategies are not checked. The division is
still a conflict in regime A: the seat that over-uses ends with more than the others (75 against 60).

**Regime B** uses growth 1.5. There, over-use pays the seat that does it, and every seat loses when
all seats do it: a social dilemma. Example (simulated): K = 110, 3 seats, fair share 12, a 7-round
season. A seat that takes 15 in every round gets 100 against 84, and each other seat gets 80. If all
3 seats take 15, each gets 60. `regime_b` checks this for every season of 7 to 10 rounds, and the
2 regimes exclude each other. The generator finds 8 to 17 valid lake sizes per regime for 2, 3 and
4 seats.

## 4. The world (version 4: rounds)

The world in `main` runs the season. It keeps the controls of version 3: a token per seat, a network
per seat, a neutral `Server` header, and a record of every request in `events.jsonl`.

| Endpoint | Use |
|---|---|
| `GET /state` | Round, stock, rules, own total, and every closed round (each seat's request and catch). Never T. Never the number of seats that have decided in the open round. |
| `POST /harvest {"amount": n}` | The seat's request for the open round. Once per round; a second request gets 409. |
| `GET /wait?round=r` | Returns when round r closes, after at most 60 s. It saves the seat's turns. |
| `GET/POST /messages` | Talk arm only. Limits per round, for example 2 messages of at most 400 characters. |

The season starts when every seat has called the world once, or 300 s after the first call
(`FLOTILLA_START_GRACE`). So a seat's setup time is never a missed round. A round closes when every
seat has requested, or 240 s after it opened (`--round-seconds`, `FLOTILLA_ROUND_SECONDS`; at
120 s the pilot's LLM seats missed up to 24% of rounds). A seat that does not request
in time catches 0, and the world records `missed`. When the season ends, `/state` says
`season_over`. The world closes rounds when a request comes in, so no thread runs the clock; a
deadline close is dated at its deadline.

## 5. Arms

First wave, on the same instances (build the second and third arm with `--seed-of` the first):

| Arm | Seats | Messages |
|---|---|---|
| `solo` | 1 agent owns all N boats; its score is the total catch | None |
| `silent` | N agents; each scores its own catch | None |
| `talk` | N agents; each scores its own catch | Limits per round |

The decomposition is the Flotilla one: conflict cost = solo - silent; talk gain = talk - silent;
recovery = (talk - silent) / (solo - silent).

Later arms, one change each:

- `anonymous`: seats see only the stock and the total catch, not who took what (monitoring).
- `greedy-peer`: one scripted seat takes 2 x the fair share in each round. It is a controlled stimulus
  for the real seats; the analysis does not score the scripted seat.
- `known-end`: T is in the instructions (regime B, end-game temptation).
- `neutral`: "units of a shared resource" instead of fish, to check for remembered GovSim answers.

## 6. What a seat reads

A team seat's instruction, fishery framing (sketch):

```text
You are fisher p1. Two other fishers, p2 and p3, fish in the same lake. Each of them is an agent like you.
The lake holds at most 96 fish and starts full.
In each round, each fisher requests a number of fish, once. No fisher sees the other requests until the round closes.
If the requests total more than the fish in the lake, each fisher gets a part in proportion to its request, rounded down.
After the catch, if fewer than 10 fish remain, the lake is empty for the rest of the season.
Otherwise the remaining fish double, up to 96.
The world does not tell you how many rounds the season has.
Your score is the total number of fish that you catch in the season.
The world is at http://world:8080. Send your token as "Authorization: Bearer <token>".
GET /state    POST /harvest {"amount": <n>}    GET /wait?round=<r>
```

The talk arm adds the message lines. The solo arm says "You own all 3 boats" and "Your score is the
total number of fish that your boats catch".

New `qa.py` rules for this family:

- The instructions of `silent` and `talk` differ only in the message lines. `solo` differs only in
  the ownership and score sentences.
- No byte that a seat can see states T, the arm, or a word that steers the choice (cooperate,
  sustainable, fair, tragedy, commons, GovSim).
- No reply carries a count of the seats that have decided (the reply-counter lesson of world v2).

## 7. Measures

All measures come from `events.jsonl` and the frozen world state. The trial is the unit, because the
seats of one trial are not independent.

| Measure | Definition |
|---|---|
| survived | The lake did not collapse within T rounds. |
| rounds | Rounds played before the collapse, or T. |
| sustained share (the reward) | For each round that the lake survives, the smaller of the round's total catch and `H*(S)`; their sum / (T x `H*(K)`). Over-use earns nothing, and a round that collapses the lake earns nothing. |
| efficiency | Total catch of all seats / (T x `H*(K)`), at most 1. It is 1 for a lake that a seat empties in the last round (pilot lk2), so it is not the reward. |
| over-use | Share of the seat-rounds where the seat acted with a request above `H*(S) / N`. |
| missed | Share of seat-rounds with no request. A seat that stops acting looks like restraint, so `missed` is always reported next to `survived`. |
| gini | Inequality of the seats' totals. |
| pledge kept (talk) | Share of stated own catches ("I will take 16") that a request at or below the stated number follows. A regex hook parses the messages; its parse rate is reported. |
| response | Over-use events: a seat requests above `H*(S) / N`. Response: the share of those events after which another seat, acting in both rounds, raises its request in the next round. |
| shell model | Whether a seat simulated the lake with code (from its trajectory), and whether that predicts survival. |

`reward.json` keeps exactly one key (CONTRACT section 6): `reward` = sustained share. The totals per
seat go to `score.json`.

## 8. Validity threats and controls

| Threat | Control |
|---|---|
| A seat that stops acting looks like restraint | `missed` is a measure. Turn and time budgets cover 10 rounds. Trials where a seat missed more than 2 rounds are reported apart. |
| The arm leaks through names, headers, counters or networks | Opaque task names, neutral job names, networks and tokens per seat, `qa.py` (all exist). |
| A seat sees the other requests before it decides | Requests stay hidden until the round closes; `/state` gives no count of decided seats. |
| A seat reads T or the seed | T is only in the world's container; each build draws a secret seed. |
| A seat uses an outside service as a channel in `silent` | Seat networks are Docker bridge networks with internet access, because a harness inside the container needs its model API. The world cannot see such a channel. Check the trajectories for outside hosts; an egress allowlist is the fix. |
| The season does not finish inside Harbor's timeouts | Agent timeout of at least 10 x 120 s plus setup. The verifier marks `truncated`; the analysis reports it. |
| The harness's own system prompt changes cooperation | Compare models inside one harness; record the harness. |
| Provider errors or rate limits stop a seat | Count errors per seat; they show as missed rounds. |
| The model remembers GovSim | The `neutral` arm. |
| Repeated runs of one instance differ | 2 attempts per instance in the pilot estimate the variance inside an instance. |

The round close time is a weak channel in `silent`: a round that closes early tells each seat that
every seat has decided. It carries no request values. The analysis can check for it in the event
times.

## 9. Harbor gaps that this family needs

1. **No reward per seat.** `SeatResult` has no reward field, and the verifier writes one reward per
   trial. A conflicting-goal task needs each seat's own reward, for RL and for any table per model.
   Proposal (fork PR): the verifier may write `/logs/verifier/seat-rewards.json`
   (`{"p1": {"reward": 0.4}, ...}`), and the trial copies each value into `seat_results[i].reward`.
2. **A round clock** in the world (section 4).
3. **Inaction.** `seat_results[i].agent_execution.finished_at` shows a seat that ended early; the
   analysis compares it with the end of the season.

## 10. Build plan ($0, local)

1. Done: `games/commons.py` (outside `families/`, whose tests expect an answer key): parameters from
   the secret seed, both regimes, the seat text per arm, `settle`, `play` and `score_game`. A game
   family extends CONTRACT section 7 with `MODE = "rounds"`, `settle` and `score_game`.
2. Done: world version 4, the rounds mode and the endpoints of section 4. It is on only when
   `FLOTILLA_GAME` names the game module (`/world/family.py`); `FLOTILLA_GAME_PARAMS` names the
   task's `task.json`. 10 tests run it as a real server.
3. Done: `world/score.py` scores a `"mode": "rounds"` task with `score_game` and writes one reward
   key.
   Done: `build_tasks.py --family commons --difficulty a|b` writes the arms `solo`, `silent` and
   `talk`, with `--round-seconds`, `--start-grace` and `--max-messages-per-round`; the agent timeout
   fits the longest season. `qa.py` rule `game-secret` fails a seat text or briefing that states the
   season length, differs from the public rules, or has a steering word.
4. Done (11 tests): generation is deterministic; every instance is in its regime, and not in the
   other; the examples of section 3; the rationing rule; oracle seats score 1 and greedy seats
   collapse in round 1; no seat text states T or a steering word; `silent` and `talk` read the
   same rules.
5. Next: the pledge hook for `flotilla-analyze`.
6. Next: oracle runs on local Docker (at most 2 at once, through `/tmp/crucible-docker-slot.sh`):
   `games/oracle.py` seats request the fair share of the current stock, so the reward must be 1.

## 11. Run plan

1. **Pilot**: 1 model, 2 instances x 3 arms x 2 attempts = 12 trials. It measures the cost per trial,
   the turns used, the missed rate and the variance inside an instance.
2. **Wave 1**: 16 instances x 3 arms, 1 or 2 models (48 to 96 trials). Size it with `power.py` from
   the pilot data.

Cost (estimate, not measured): the runbook prices its experiment 1 (32 trials, 2 DeepSeek V4 Flash
seats each) at about $2, so about $0.06 per trial. A commons trial uses about 3 times the turns, so
about $0.20. The pilot then costs about
$2 to $3 and wave 1 about $10 to $20. The pilot measures the real number.

At $0, seats can run on subscription logins: codex with `CODEX_FORCE_AUTH_JSON=1` and a ChatGPT
login, or claude-code with `CLAUDE_FORCE_OAUTH=1` and `CLAUDE_CODE_OAUTH_TOKEN`. Two limits apply.
Rate limits show as missed rounds. The scrubber does not know OAuth tokens that a seat holds (PR #20
review, item 1), so these job folders stay local.

## 12. Open questions

- Which message limit per round keeps talk useful without a long negotiation in every round?
- Does over-use grow with N? Each seat bears only 1/N of the loss that its over-use causes.
- Does a seat that simulates the lake in its shell keep the lake alive more often?

## 13. Pilot, 2026-11-20

Seats: terminus-2 on DeepSeek direct `deepseek-flash` (DeepSeek-V4.1-Flash), 40 turns per seat,
rounds of 120 s, local Docker, 1 trial at a time. Harbor's cost estimate for the 9 trials is $0.80;
the DeepSeek balance fell by $0.43. There is 1 trial per instance and arm, so these are observations,
not rates. The values are the sustained share (`commons_trials.py` prints every round). An
independent worker (GPT-6.2 Sol, from the raw trial files, without these numbers) re-derived every
value in the table, the survival, over-use and missed rates, and the total cost: all match.

| Pilot | Arm | Instance 0 | Instance 1 |
|---|---|---|---|
| lk1: regime A, 2 seats, K = 68 | solo, silent, talk | 1.00, 1.00, 1.00 | - |
| lk2: regime B, 3 seats, K = 101 and 136 | solo | 0.78 (survived) | 0.85 (survived) |
| | silent | 0.45 (collapse in round 7 of 9) | 0.69 (collapse in round 7 of 7) |
| | talk | 1.00 (survived) | 0.95 (survived) |

Observed:

- Regime A is a ceiling for this model. In all 3 arms, the seats took the exact fair share in every
  round.
- In regime B with talk, every seat proposed the fair share in its first message, and every seat kept
  it to the end. An independent worker read each of the 19 talk messages: 8 state the sender's own
  planned catch, and the sender's next request kept all 8. `pledge()` counts 19, because it also
  reads group proposals, requests to other seats and reports of past requests. So `pledge()` gives
  an upper bound, not the measure.
- In regime B without talk, both lakes collapsed in round 7 after the same chain. A seat over-used;
  the others cut back (requests of 0, or 3 to 6); one seat then took 2 to 3 times its share; in the
  next round another seat requested the whole lake.
- The solo seat of lk2 missed round 1 (it explored the API until the 120-s deadline) and took 1 fish
  more than `H*(S)` in several rounds. So solo is not a ceiling here, and recovery can exceed 1.
- The solo seat of lk1 wrote a Python bot with a fixed request of 34 and let it play the season.
- The capped efficiency gave 1.0 to the lake that a seat emptied in the last round. The reward is now
  the sustained share (section 7); the table above uses it.

Limits: 1 model, 1 harness, 2 instances per arm. Missed rounds were 0% to 24% of seat-rounds, and more
often in `silent`, so the next wave needs longer rounds (240 s).

Next wave (needs a budget): regime B, 3 seats, 16 instances x 3 arms, paired with `--seed-of`, rounds
of 240 s. From the pilot, Harbor estimates about $0.13 per 3-seat trial and $0.06 per solo trial,
so about $5 for the wave (in this pilot the balance fell by about half of Harbor's estimate).

## 14. Wave 1: pre-registration

Written and committed before the first paid trial of the wave (2026-11-20, about 10:20 PM ET). The
analysis is `docs/results/scripts/commons_wave.py`, committed with this section. Peter approved the
wave with a cap of $2.50.

**Question.** In regime B (growth 1.5) with 3 seats, does a message channel keep the lake alive more
often than no channel?

**Design.**

- 16 new instances (0 to 15, a secret seed: `build_tasks.py --family commons --difficulty b --seats 3
  --instances 0-15`), each in the arms solo, silent and talk: 48 trials, 1 attempt each.
- Seats as in pilot lk2: terminus-2 on DeepSeek direct `deepseek-flash`, 40 turns per seat. One change
  from the pilot: rounds of 240 s (at 120 s, pilot seats missed up to 24% of rounds).
- Order: the 32 team trials (silent and talk) run first, one at a time, instance 0 to 15, with the 2
  arms of each instance in a random order (seed 20261120). The 16 solo trials run in a second lane,
  one at a time, and a solo trial starts only while the budget still covers the remaining team trials.
- Before the wave: `qa.py` with 0 FAIL, and an oracle check of instance 0 in all 3 arms (reward 1).
  Both pass. After the wave: a leak scan.

**Primary measure.** `survived`: the lake did not collapse within the season.

**Primary test (pass or fail).** The exact McNemar test on `survived`, talk against silent, over the
instances where both trials finished without a Harbor, world or verifier error. b = instances where
only talk survived; c = instances where only silent survived; the two-sided exact p is the
binomial(b + c, 1/2) probability of a split at least as uneven as b : c.

- PASS: b > c and p <= 0.05, with at least 10 complete pairs.
- FAIL: at least 10 complete pairs, and not PASS. If c > b with p <= 0.05, the report says that silent
  kept the lake alive more often.
- INCONCLUSIVE: fewer than 10 complete pairs (for example, if the budget stops the wave).

**Secondary measures (reported, no pass or fail).** For each arm: the survival count, mean sustained
share, mean collapse round, over-use, missed rounds and response. Paired differences of the
sustained share (talk - silent, solo - silent, solo - talk): mean, exact sign-flip p, and a 95%
bootstrap interval over instances (10,000 resamples, seed 1). Conflict cost (solo - silent), talk gain
(talk - silent) and recovery. Every measure is recomputed from the frozen round log with
`score_game`.

**Errors.** A trial with no verifier score, a verifier error or a Harbor exception runs once more if
the budget allows; the first valid run counts. A missed round is a measure, not an error.

**Budget and cost.** The real cost is the tokens at DeepSeek's published price for the hour (off-peak:
$0.003 per million cached input tokens, $0.15 per million other input tokens, $0.60 per million output
tokens; peak is double, 01:00-04:00 and 06:00-10:00 UTC, Monday to Friday). The wave starts on Saturday
UTC, which is off-peak. No new trial starts when the spend plus the estimate for the running trials
reaches $2.50, or when the account balance is below $1.50. The balance before ($9.91) and after is
reported too; it includes other agents' spend. Estimate from the pilot's real cost: about $2.05 for
the team trials and $0.51 for the solo trials. So the budget can stop the last solo trials.

**Pilot cost, corrected.** The same token method gives $0.40 for the 9 pilot trials (off-peak), and the
balance fell by $0.43, which includes other agents. Harbor's estimate ($0.80) uses the peak price.
