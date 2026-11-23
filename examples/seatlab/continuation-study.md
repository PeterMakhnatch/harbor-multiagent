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
| First-hour verifier grade, unavailable to Flash | 0.45121 | 0 |
| Final grade, unavailable before time ran out | 0.51046 | +0.05924 |

The activity-only supervisor selected the busiest board in all 9 sets, including ties. Adding text changed the selected grade in 5 sets: 2 gains and 3 losses. The other 4 tied. The mean difference was −0.02398; the one-sided sign test for a benefit gave p=0.8125. All 27 first-hour verifier grades were zero. These observations do not show that chat harms teams. We changed the **supervisor's input**, not communication between the original agents. None of these policies qualifies for autonomous budget allocation.

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

Registered before reading the 2-hour and 3-hour grades of these multi-seat teams. This is a retrospective analysis of the same release. Its 1-hour and 4-hour results are already known. The question is whether an operator with **240 team-minutes** should start several teams or give one team all 4 hours. Compare policies only within the same model and team size. Keep all 9 sets of 3 runs. A seat-minute is one team-minute times the number of seats in that team.

**Primary comparison:** start 2 teams for 60 minutes each. Keep the one with more public first-hour posts for another 120 minutes. Use its recorded **3-hour** grade. Average tied selections instead of choosing a favorable run. Average over all 3 possible pairs within each set, then give each of the 9 sets equal weight. Compare with a uniformly chosen team run for 4 hours. Require a mean gain of at least 0.03 and a one-sided exact sign test across sets of p≤0.05 before recommending a prospective replication. The 27 pairs are not independent.

**Diagnostics:** for those pairs also compute the mean grade from uniform selection at 1 hour and the best possible 3-hour selection with hindsight. Separately, start 3 teams for 60 minutes each and keep one for 60 more. Use the published supervisor's sealed choice and its **2-hour** grade. Compare this with the busiest board, uniform choice and perfect 2-hour hindsight in each set. Average all tied winners. Do not choose a better threshold, branch count, cutoff or selector after reading those grades.

All 3 schedules use 240 cumulative team-minutes. They finish after 240, 180 and 120 wall-clock minutes respectively, assuming enough compute. They do not necessarily cost the same in tokens or dollars. The checkpoint grades evaluate published work. No sandbox was resumed, no job was canceled, and the hypothetical supervisor cannot use hindsight.
