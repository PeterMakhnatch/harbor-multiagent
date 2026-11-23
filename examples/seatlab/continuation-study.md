# Can an operator choose which team to continue after 1 hour?

Study contract, 2026-11-23. This is a retrospective selection experiment on **Seed to Profit**. It does not start or restart a Bespoke sandbox. The original teams and their four-hour grades are real. The supervisor decisions are new. Bespoke has already published aggregate results and some example scores; this is not a previously unseen dataset. This contract precedes collecting any supervisor replies or calculating the selection result.

**Question.** With budget for 1 continuation, can a supervisor pick a better company from 3 parallel teams using only their public board before minute 60?

**Population.** Use all 27 communicating multi-seat runs in the [public rollout index](https://benchmarks.bespokelabs.ai/multi-agent-coordination-bench/public-rollout-data/index.json). Form 9 disjoint sets of 3: each original model and team size (2, 4, 8) defines a set. Do not compare different models or team sizes in a set. Exclude single-seat runs and board-disabled runs by design, never by outcome. A set is the unit of analysis.

**Choices and information.** The supervisor sees a count and up to the last 4 public posts per candidate from strictly before minute 60, including the minute and a local seat number. Limit each post to 320 UTF-8 bytes. Exclude private messages, absolute timestamps, run IDs, model names, provider costs and grader marks. Prices discussed by the simulated businesses are public evidence. Quote board text as data. A post is a claim, not evidence that another agent read or acted on it. Run 3 cyclic presentations per set so each candidate appears in each position once. The supervisor returns one position per presentation, without tools, feedback or a second attempt. Aggregate by plurality. Split ties uniformly in expectation.

**Controls.** The primary control picks uniformly among the same 3 recorded continuations. Its expected score is their mean. Equivalently, randomly assigning the visible cards to continuations destroys the identity signal without changing the prompts. This is an exact expectation, not a second batch of model replies. Also report the busiest public board, the best 1-hour verifier mark, and the best 4-hour grade. Average exact ties. The 1-hour mark is an owner-side comparator; the 4-hour maximum is hindsight, not a deployable strategy.

**Pass rule.** Require all 27 first replies to be valid. Require a mean improvement of at least 0.03 on the 0–1 final grade over uniform selection. Also require a one-sided exact sign-test p-value ≤0.05 across the 9 set differences, excluding ties. This is a small, selected benchmark population; the test does not establish a general effect across tasks or models. Report an incomplete or negative result rather than promoting an unproven policy. Report position agreement and both simple controls even when the gate passes.

**Additional guard, fixed before the first supervisor call.** A random candidate can exceed the set mean with probability 1/3 or 2/3. The sign test alone is not a test of random assignment. Also require one-sided p ≤0.05 when the scorer permutes the association between the 3 cards and the 3 recorded outcomes independently within each set. Keep observed plurality ties and count each assignment with its true multiplicity. Apply both guards; do not replace a failed guard.

**Prespecified diagnostic: words or activity?** Make 27 new, independent Flash calls with the same positions, counts, times and authors. Replace each quoted public post with the same `[text withheld]` string. Compare each set's selected final grade with the full-text choice. Count evidence for text content only with complete first replies in both arms, a mean paired advantage of at least 0.03, and a one-sided exact paired sign test ≤0.05. A negative or mixed result is not proof that the messages contain no useful information. Withholding changes prompt length. These are 9 paired sets, not 54 independent teams. Use the same $0.27 worst-case call authorization for this separate diagnostic, within the existing shared account guard.

**Resource boundary.** The hypothetical allocation uses 360 cumulative team-minutes per set: 3 × 60 + 1 × 180. Following all 3 teams to the end uses 720. Both have a 240-minute wall-clock horizon with enough machines. This is not a measurement of time saved, token use or dollars saved. The fork never kills a job. It emits decisions for evaluation. The data do not contain resumable company snapshots. The real Bespoke harness can write them on runs you own.

**First worker.** Use DeepSeek direct Flash through the existing SeatLab meter. Reserve at most $0.27 for 27 turns at its $0.01 hold per turn. Stop on a provider or balance failure. Record actual cache-hit, cache-miss and completion tokens. Other workers can return the same request envelope without this provider. Never send `owner.json` or a source cache to a blind worker.

Source: [Bespoke release](https://benchmarks.bespokelabs.ai/multi-agent-coordination-bench/) and [stock business task](https://github.com/bespokelabsai/multi-agent-coordination-bench/tree/dbec43ff757c86c17077e61b37c7f584c880bdee/tasks/seed-to-profit). The public viewer shortens messages. This study makes no claim about unpublished messages, dollar savings or the causal effect of team communication.

## Result: do not stop a team on this evidence

All 54 first replies were valid. With public messages, the supervisor selected companies with a mean grade of **0.45386**, versus **0.45121** for uniform selection. The gain was 0.00265, below the required 0.03. The exact assignment test gave p=0.5133. There were 6 gains, 2 losses and 1 tie; the sign test gave p=0.1445. A single loss on a four-seat set outweighed several small gains.

| What the selector could see | Mean selected grade | Difference from uniform |
|---|---:|---:|
| Nothing: uniform choice | 0.45121 | 0 |
| First-hour activity and message text: Flash | 0.45386 | +0.00265 |
| First-hour activity, no message text: Flash | 0.47784 | +0.02663 |
| Public post counts only: busiest team | 0.47784 | +0.02663 |
| First-hour live-company mark, withheld from Flash | 0.45121 | 0 |
| Final grade, unavailable before time ran out | 0.51046 | +0.05924 |

The activity-only supervisor selected the busiest board in all 9 sets, including ties. Adding text changed the selected grade in 5 sets: 2 gains and 3 losses. The other 4 tied. The mean difference was −0.02398; the one-sided sign test for a benefit gave p=0.8125. All 27 first-hour **live-company marks** were zero. These observations do not show that chat harms teams. We changed the **supervisor's input**, not communication between the original agents. None of these policies qualifies for autonomous budget allocation.

The [54 first-call costs](results/continuation-meter.json) total **$0.008075562**. The [derived results](results/continuation-results.json) include every set. The [input receipt](results/continuation-inputs.json) was public before the first model call. Public files contain choices and hashes, not the source messages.

**What would this cost an operator?** Starting one team at minute 0 and keeping it costs 240 team-minutes. Screening 3 teams, then keeping one, costs 360: **50% more than the uniform baseline**. Finishing all 3 costs 720. These are cumulative team-minutes, not elapsed minutes, tokens, or dollars. At a fixed team size of 2, 4, or 8, multiply by that size to get seat-minutes. The archived trials do not measure actual savings.

**What can the comparison establish?** There are 9 sets, one task, one supervisor and one draw for each presentation. One set changes the sign test substantially. A failed gate does not establish that boards are useless. The text arm and masked arm also have different lengths. Even random text-driven choices could appear better than a poorly performing masked policy. Treat that contrast as descriptive, not as a causal estimate of the value of words. For deployment, the LLM must beat the free count-only selector, not just a uniform choice. Its observed score was below the count-only selector. Its gain over uniform did not pass the gate. Public posts can contain self-reported results; these are not independently verified grades.

## Give another supervisor a real decision

From the fork checkout, run this with Python 3.12 or later. It needs no Harbor install, model key or Docker. `fetch` downloads the Bespoke release once. After that the scoring path is offline.

```sh
w=$(mktemp -d)
m=examples.seatlab.continuation
python3.12 -BS -m "$m" fetch "$w/cache"
for view in posts activity; do
  python3.12 -BS -m "$m" prepare "$w/cache" "$w/$view" --view "$view"
  python3.12 -BS -m "$m" score "$w/$view" \
    "examples/seatlab/results/continuation-$view-first.jsonl" > "$w/$view.json"
done
python3.12 -BS -m "$m.contrast" "$w/posts.json" "$w/activity.json"
```

For a new supervisor, emit one shard per worker. Send **only** its JSONL, not the checkout, source cache or owner directory. Keep the 3 presentations of a set on one shard. Use independent first turns without memory between presentations. Return `{"id":...,"prompt_sha256":...,"reply":"{\"pick\":2}"}` per request. The scorer checks for duplicate IDs and stale prompts, and lists missing decisions instead of filling them in.

```sh
python3.12 -BS -m "$m" emit "$w/posts" --shard 0/3 > "$w/worker-0.jsonl"
```

Use `1/3` and `2/3` for the other workers. Pass their reply files as separate arguments to `score`. Never rerun a failed first attempt to improve coverage. The optional `python -m examples.seatlab.continuation.direct --help` worker uses a private host ledger, records the first reply, and enforces a campaign limit. Restarting with the same book skips closed requests. An attempt interrupted after its claim remains unknown; it is not billed again by that book. The worker is not a background service.

## Is screening worth doing at equal cumulative team time?

**Correction: the published marks cannot answer the early-stop question.** We registered a comparison at 240 cumulative team-minutes. Its initial analysis treated an interim mark like a terminal reward. They measure different things. We withdraw the earlier −0.10332 effect, its sign test and the recommendation to give one team the full time. This release does not establish whether early stopping helps or hurts.

The [stock verifier](https://github.com/bespokelabsai/multi-agent-coordination-bench/blob/dbec43ff757c86c17077e61b37c7f584c880bdee/tasks/seed-to-profit/task/tests/verify.py#L334-L411) reads current company value at an interim mark. It does not finish the remaining months, check a replay or grade the team's business playbook. At the deadline the verifier does all 3. The final grade combines the replay-checked live result and the playbook result with equal weight.

| Schedule at 240 cumulative team-minutes | End of agent work | Published reading for the selection | Final reward for this schedule |
|---|---:|---|---:|
| Run 1 team for 4 hours; choose uniformly at the start | 240 min | Terminal verifier | 0.45121 |
| Start 2 teams; keep the busiest after 1 hour | 180 min | Live company only: 0.34789 | Unknown |
| Start 3 teams; keep the supervisor's choice after 1 hour | 120 min | Live company only: 0.08830 | Unknown |

We averaged all 3 pairs within each set, then weighted the 9 sets equally. Ties share the selection. These are published readings of uninterrupted teams, not trials we stopped. The scheduled seat-time does not establish dollars spent. Even the largest interim mark is not a bound on the terminal reward.

On a job you own, enable `--ak grade_at` at launch. The [harness writes completed marks to the trial directory](https://github.com/bespokelabsai/multi-agent-coordination-bench/blob/dbec43ff757c86c17077e61b37c7f584c880bdee/harness/README.md#graded-marks). A mark can arrive after the scheduled time. To test stopping, retain the company state and team submission at the cutoff and run the same **terminal verifier** on both schedules. The published boards and scalar marks are not enough.

Recompute the observed readings from the pinned public cache and the sealed replies. This needs no provider, container or simulator.

```sh
d=examples/seatlab/results
python3.12 -BS -m examples.seatlab.continuation.equal_time "$w/cache" \
  "$d/continuation-inputs.json" "$d/continuation-posts-first.jsonl"
```

The [typed results](results/continuation-equal-time.json) distinguish observed live marks from final rewards. Only the 4-hour run has a terminal reward.

## What if all the smaller teams finish?

This is a separate **retrospective** resource check, not a test on unseen teams. The 4-hour grades were already published and used above. Unlike early screening, every team in this comparison runs for 4 hours. Ask whether two isolated half-size teams offer enough extra reward to justify testing a selector that does not see the final verifier grade.

Use each of the 3 original models with a total budget of 4 or 8 seats. In each of the 6 settings compare the mean final grade of one full-size team with all 3 unordered pairs of half-size teams. A pair uses the same seat count and 4-hour clock. Grade uniform output selection, the output of the team with more first-hour public posts (average ties), and perfect output selection. Average the 3 pairs first, then weight the 6 settings equally. Report each model and seat budget separately. The 4-seat source teams appear on both sides of different settings. There are no paired seeds. Do not assign an independence-based p-value or claim equal tokens or spending.

Before calculating this comparison, set a spending gate: only design a paid, blinded selector if perfect selection gains at least 0.03 over one full-size team on average and is better in at least 4 of 6 settings. This is a feasibility bound, not a result about what an actual selector knows. If the bound passes, require any later selector to beat the uniform and count-only controls as well as the single full-size team. Do not use the released final grade to make an operator decision.

**Result.** Both half-size teams can finish in this allocation. Uniformly choosing their output scored 0.44027. Choosing by the busiest first-hour board scored 0.45347. One full-size team scored **0.45582**. Perfect selection between the smaller teams scored **0.49127**, an advantage of 0.03545, positive in 5 of 6 settings. The spending gate passes. The two available selection rules still lose to the single team. This is room to test a better selector, not evidence that the portfolio should ship.

```sh
python3.12 -BS -m examples.seatlab.continuation.portfolio \
  examples/seatlab/results/continuation-equal-time.json
```

The [exact pair results](results/continuation-portfolio.json) include every model and seat budget. A 4-seat budget gives either 1 team of 4 or 2 teams of 2. An 8-seat budget gives either 1 team of 8 or 2 teams of 4. Running both smaller teams does not shorten the 4-hour deadline. It doubles the number of team starts.

### Can an operator use the teams' own score reports?

Before reading the final-hour board text, register one free selector. Look only at broadcasts by enrolled seats during minutes **180 inclusive to 240 exclusive**. A qualifying claim explicitly names `held-out score`, `validation score`, or `verifier score`, followed by `:` or `=` and one grade from 0 to 1. Match case-insensitively. Accept spaces, underscores and hyphens in a name. Accept decimals such as `.72` and `0.72`; reject percentages, ranges, scientific notation, and negative values. Do not extract capital, profit, estimates from unlabeled numbers, or values from private messages. Never use a future verifier mark.

For each pair, require the same named measure on both boards. Use the first shared measure in this order: held-out, validation, verifier. Use the last qualifying occurrence of that measure on each board. Count a tie as covered and average its two possible outputs. If either board lacks a comparable claim, abstain and use uniform choice; keep that pair in the denominator. Prespecify a useful-result test: at least 12 of 18 pairs have comparable claims, the mean selected grade beats a single full-size team by at least 0.03, and it exceeds both uniform and first-hour count selection. Report coverage, abstentions, and grades even if no agent uses this format. Do not add spellings after inspecting the posts. An agent's claim is not an independent test result.

Separately, test a blind two-option reader when an eligible model is available. Give it the public count and up to the last 4 broadcasts in the same final-hour window from each of the 2 completed candidates. Show both presentation orders in independent calls. Do not supply private posts, run IDs, verifier grades, or the third team's board. The owner maps each order back to the actual team. Average the grades selected in the 2 orders, then average over all 18 pairs. Require 36 valid decisions, a gain of at least 0.03 over the full-size team, and a grade above both uniform and first-hour-count selection. Report how often the choice survives an order swap. A missing reply means incomplete, not a tie or a loss. Do not substitute one method's replies for another's.

### A complete free worker: count activity in the final hour

The projection found 147 public posts and no comparable labeled score. Before joining final-hour counts to pair winners, register a second reader. It sees the same blind packets and chooses the candidate with more public posts during minutes 180–240, excluding minute 240. An exact tie chooses the first displayed candidate. Averaging the reversed orders gives uniform choice on a tie. Run all 36 decisions on separate packet shards with no access to grades. Use the same reader gate: at least +0.03 versus the full team and a grade above both uniform choice and the first-hour count rule. This is a deterministic worker acting on real agent traffic. It is not an LLM judge or an independent replication of these released teams.

**Result.** The 18 candidate teams made 147 final-hour public broadcasts. The projection excluded 255 private messages. Six teams had no public posts in this window. No pair reported the same qualifying score. The registered claim rule abstained on all 18 pairs and received the uniform grade, **0.44027**. This tests a reporting format; it does not show that the agents had nothing useful to say.

Three independent count-only workers returned all 36 first choices. They chose the same team under both presentation orders for 12 of 18 pairs. The selected mean was **0.44518**, compared with 0.45347 for first-hour counts and 0.45582 for one full-size team. The free worker failed the gate. No model evaluated these 36 final-hour packets. The earlier 54 Flash calls concern a different question.

## Give completed team pairs to independent workers

Use this to compare your own selection method with the count-only worker. Give each worker its packet file. Keep the archive and scorer separate from the worker. The packets contain short, untrusted excerpts; do not give a reader tools. This example uses 3 local processes and no model.

```sh
m=examples.seatlab.continuation
w=$(mktemp -d)
python3.12 -BS -m "$m" fetch "$w/cache" || exit 1
pids=""
for i in 0 1 2; do
  python3.12 -BS -m "$m.final" emit "$w/cache" --shard "$i/3" > "$w/requests-$i.jsonl" || exit 1
  python3.12 -I -S examples/seatlab/continuation/activity_worker.py \
    < "$w/requests-$i.jsonl" > "$w/returns-$i.jsonl" &
  pids="$pids $!"
done
for pid in $pids; do wait "$pid" || exit 1; done
python3.12 -BS -m "$m.final" score --reader final-hour-count "$w/cache" "$w"/returns-*.jsonl
```

The owner joins each first reply to its request ID and prompt digest. Missing or invalid decisions do not receive a grade. An unknown or duplicate return is an input error. The archived [first replies](results/final-board-activity-first.jsonl), [worker hashes](results/final-board-workers.json) and [scored batch](results/final-board-activity.json) contain no source messages. The [claim result](results/final-board-claims.json) also shows the model-reader slot as incomplete.

### Can the owner use a 3-hour company mark to choose a completed output?

**Question, fixed before joining 3-hour pair rankings to final grades.** At equal seats and 4 hours, can an owner choose the better small-team output using a live signal rather than chat? This is a retrospective test. We have already seen these teams' marks and final grades in other analyses. We do not claim a new cohort.

Use the same 18 pairs in 6 model/seat-budget settings as the full-length comparison. Both teams work for all 4 hours. Once both 3-hour marks exist, choose the company with the higher mark. Average the candidates on a tie. Missing or invalid marks make the comparison incomplete; a missing mark is not zero. Do not use 1-hour, 2-hour or 4-hour values in the decision. Do not forward either mark to the teams.

Evaluate only with the 4-hour terminal verifier. Average the 3 pairs per setting, then average the 6 settings. Pass only if the mean gains at least 0.03 over one full-size team, beats uniform and first-hour count selection, and beats the full-size team in at least 4 of 6 settings. Report ties and all 6 setting results. Do not infer a p-value from overlapping pairs, equal dollar cost, or what would happen if a team stopped early.

### Does the same signal transfer when there is no team chat?

We inspected the release labels, but not the grades of the following 18 runs for this analysis. Register this test before scoring the coordinated-team mark selector. Use the remaining 9 single-agent runs and 9 eight-seat runs labeled `Without communication`. Each of the 3 models has 3 runs per design. In this business task, the board-off policy refuses messages; the 8 seats still share the company and repository. Do not describe them as 8 independent businesses.

In each of the 6 model/design settings, keep all 3 unordered pairs. Both candidates work for 4 hours. Choose the higher 3-hour live mark; average a tie. Grade only the 4-hour terminal result. Compare with uniform choice between **the same 2 candidates**. A missing mark or final reward makes the test incomplete. Do not choose another cutoff.

Pass the transfer test if the equally weighted mean gain over uniform is at least 0.03 and positive in at least 4 of 6 settings. Report the attainable gain from perfect choice and the reward of each design separately. This tests output selection in another cohort. It does not compare against one larger team, estimate the effect of disabling communication, or estimate a dollar saving.
