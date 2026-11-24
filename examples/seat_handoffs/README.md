# Did the receiver get the report before it committed?

Use this reader after a Harbor team trial. For a handoff, it reports one row per declared message and receiver. For a [shared schedule](#which-teammate-changed-the-standing-answer), it reports changes between accepted writes. Both workflows distinguish evidence from the final score. Run from the repository root with Python 3.12 or later. No Harbor installation, credentials or model calls are needed.

```bash
python3 -m examples.seat_handoffs events "$trial_dir" --boundary acknowledged
python3 -m examples.seat_handoffs events "$trial_dir" --boundary returned --json
```

`trial_dir` is a native trial directory, not its parent job. Supply several directories as separate arguments to compare trials. A missing journal is an error, not a team with no messages. A file argument instead of a directory inspects a journal directly: its grade is the producer's closing grade, not a verified native result.

To try it without Docker, run `python3 -m examples.seat_handoffs events examples/seat_handoffs/samples/*.jsonl --boundary acknowledged`. The two journals are unmodified verifier exports from the `confirm` control at seed 39. Both bookers scored 1. One acknowledged the revision after it committed; the other never acknowledged it.

| Boundary | What the evidence establishes | What it does not establish |
|---|---|---|
| `returned` (default) | The world prepared a reply containing the report. | The network delivered it or the receiver processed it. |
| `acknowledged` | The receiver posted the receipt from that reply. | The model used or understood the report. |
| `model_request` (archived teams only) | The audited input to the receiver's decision call contained the report. | The model followed the report or it caused the grade. |

`timing=before` and `after` use event order, not host clocks. A complete journal can also establish that no commit occurred. An open or interrupted journal gives `unknown` for missing events. A closed journal with no acknowledgement establishes absence of **acknowledgement**, not absence of delivery. An older producer that never recorded acknowledgements cannot establish their absence, even after closure.

For a native trial, inspect `native_trials` in the JSON. The reader keeps the native trial ID and the world's run ID distinct. It reports a native reward only if `result.json` marks that reward valid. A missing or invalid reward stays `null`, including when the task's own closed journal contains a reward. The [revised-dispatch task](../tasks/revised-dispatch/README.md) also reports whether the booker changed its draft. None of these outputs include the slots, receipts, message contents, or model responses.

## Inspect how an inaccurate report affects a booking

Run this [scripted native control](../configs/features/revised-dispatch-report-error.yaml), then inspect the trial name printed by Harbor:

```bash
uv run harbor trial start -c examples/configs/features/revised-dispatch-report-error.yaml
python3 -m examples.seat_handoffs events trials/<trial-name> --boundary acknowledged --json
```

Read `native_trials[0].decision`. The reporter sent an inaccurate revision. The booker acknowledged and matched it, then earned a valid zero. Change only the booker policy to `ignore`. It still acknowledges the revision but now books the correct slot. Neither result implies that a model understood or distrusted the report.

The [original registration](samples/report-accuracy-registration.json) and [measured results](samples/report-accuracy-controls.json) cover 10 first attempts using stock Harbor. They include different booker policies that made the **same** correct choice, and 2 trials where agreement is unknown. A late or missing acknowledgement never counts as disagreement. Old version 1 decision files have no agreement measurement.

## Inspect a whole Harbor job, including missing evidence

Use `job` instead of a shell glob when reporting a cohort. A glob sees only the directories you collected. Harbor's job lock includes every scheduled trial, even when no directory is available.

```bash
uv run harbor jobs start -c examples/configs/features/revised-dispatch-job.yaml
python3 -m examples.seat_handoffs job "jobs/<job-name>" --boundary acknowledged
python3 -m examples.seat_handoffs job "jobs/<job-name>" --boundary acknowledged --json
```

The [example job](../configs/features/revised-dispatch-job.yaml) runs 3 scripted teams, one at a time. No key is required. Each booker keeps its stale draft. These are valid zero rewards, not missing trials. To run different teams in parallel, give each worker its own job name and config. Set the seed, variant and participants in each config; keep the same seed across conditions.

The reader checks the job UUID, directory name and native trial lock. It groups identical locked inputs rather than assigning an order to indistinguishable repeats. **Missing slots** have no attributable directory in this copy. They may have run elsewhere. A matched trial can still have no journal, no final seal, or no valid grade.

**Valid native grades** count scores from attributable native results, even when the handoff journal is missing. **Usable trials** have both a sealed journal and a valid native grade. Only these contribute to the joint handoff-and-reward summary. Read the trial rows for partial observations.

Use a stable copy to compare workers. In a live job, Harbor can still write files. A retry can replace a trial directory. The reader cannot recover the earlier attempt from a later copy. If the job has no native lock, the reader cannot establish a denominator.

## Try the two frozen archives

These are two specific published studies, not general parsers for arbitrary agent traces. From a checkout with Peter's fork as `origin`:

```bash
git fetch --no-tags origin experiments/seatlab-report-budget
rev=eead793e3e59c42b4ea619a1ce10cab65f147c92
scratch=$(mktemp -d)
git show "$rev:examples/seatlab/results/report-budget/rows.json" > "$scratch/pricing.json"
git show "$rev:examples/seatlab/results/wait-for-peer/calendar-subscription-rows.json" > "$scratch/booking.json"
python3 -m examples.seat_handoffs bespoke "$scratch/pricing.json"
python3 -m examples.seat_handoffs calendar "$scratch/booking.json"
```

The adapters check the exact release bytes before trusting the study's prior audit. They do not rerun teams or reconstruct model requests. In pricing, 46 of 48 teams passed; 10 of those 46 had no report in the finance decision request. In booking, all 24 teams received a report before booking and all 24 passed. The booking data has no undelivered live comparison. In JSON, use `summary.success_without_on_time_by_message` for the message of interest. A team that receives an initial report can still miss its revision.

## Which teammate changed the standing answer?

Flotilla's stock schedule world grades the **last accepted write**, not the best answer the team produced. A teammate can fix a wrong answer, replace a correct answer, or change its spelling without changing its grade. Use this operator when you have saved trials but do not have their original task folders. It does not open a task, read a trajectory or run a grader.

```bash
python3 -m examples.seat_handoffs schedule-jobs "$job_a" "$job_b"
python3 -m examples.seat_handoffs schedule-jobs "$job_a" "$job_b" --include-timeouts --json
```

Supply whole native jobs when possible. Their locks distinguish scheduled slots, matched trials and missing directories. A finished verifier grade can survive a seat timeout. The default shows these grades but excludes the affected teams and their answers from the comparison. `--include-timeouts` includes only coherent, explicitly recorded agent timeouts with a valid frozen grade. Other errors are not scored as misses.

For individual trials, use `schedule` instead of `schedule-jobs`. This mode has **no job denominator**. Here is a public example using 4 original scripted teams from the fork. It needs no Docker:

```bash
git fetch --no-tags origin flotilla/lab
scratch=$(mktemp -d)
rev=71ddf40b14ea81dd8eb0c32663e11abfe21cd1e3
fixture=flotilla/analysis/tests/data/oracle-check-2seat
git archive "$rev" "$fixture" | tar -x -C "$scratch"
python3 -m examples.seat_handoffs schedule "$scratch/$fixture"/schedule-*/
```

The example has 4 cross-seat transitions. No transition could have repaired a wrong answer. Between 0 and 2 could have replaced a correct answer. All 4 final grades are known. **Knowing the final grade is not the same as knowing the previous grade.**

`before_correct` and `after_correct` are `null` when there is not enough evidence. A pass identifies the unique correct slot for this exact native task checksum. A zero rules out only the final submitted slot. The command shares these witnesses across independent trials with identical task checksums, never across similar task names or seeds. Duplicate trial IDs and conflicting witnesses do not count. An answer spelling that the two published schedule rubrics can grade differently puts that trial outside scope.

`summary.bounds` gives the lowest and highest possible count for each metric. Each candidate truth must satisfy **all** the observed grades for that task. Different bounds need not hold simultaneously. Only a write by a different seat counts as a team repair or replacement.

`peer_return` describes a different observation. `new_return` means the world first included a message from the previous writer in a reply to the new writer between the two writes. An earlier return is `previous_return`; reading the same message again does not make it new. `sent_not_returned`, `not_sent` and `unknown` are distinct. A missing journal does not erase a frozen write. The stock log has no world UUID: matching ordered records is a consistency check, not authentication. No label proves that a model read a message, acted on it or caused the grade.

### What this recovered from model teams

We inspected 8 existing Flotilla jobs. Their locks listed 192 slots. We found 180 trial directories and 171 usable frozen grades. Both seats finished in 117 trials. In 54 others, at least one seat timed out despite a valid grade. The other 9 directories lacked enough verifier evidence.

| Teams included | Cross-seat writes | Known repairs | Known replacements of a correct answer | Still unknown | Possible final replacements of a correct answer |
|---|---:|---:|---:|---:|---:|
| Both seats finished (117) | 138 | 9 | 15 | 40 | 9–28 |
| Also include 54 graded timeouts (171) | 360 | 18 | 24 | 124 | 11–29 |

In the first row, inspecting each trial alone left 77 writes unknown. Sharing evidence from the **same task checksum** resolved 37: 5 repairs, 13 replacements and 19 unchanged. An independent check used the original scheduling rules and exact task contents for 116 of the 117 teams. Every known cross-seat change agreed (98/98). One team had no matching task copy. Its only cross-seat change stayed unknown. An independent reader also matched all 299 adjacent server-return labels in the 117 teams. Neither check establishes what a model saw or why it chose an answer.

The [aggregate evidence](samples/schedule-changes-cohort.json) includes the registration, enrollment losses, uncertain counts, keyed checks and sensitivity to timeouts. The original trials are not distributed here. Use the [Flotilla analysis](https://github.com/PeterMakhnatch/harbor-multiagent/blob/71ddf40b14ea81dd8eb0c32663e11abfe21cd1e3/flotilla/analysis/README.md) when the task folders **are** available.

### Is first agreement a safe time to stop?

**Do not stop a live team just because its seats agree.** In one archived team, the two seats first agreed at submission 36. Their joint answer was wrong. The standing answer at submission 57 was correct. One seat later timed out, but the native verifier recorded a valid success. Excluding all such teams hides this case.

Use `--checkpoints` with `schedule` or `schedule-jobs`. For example, after extracting the 4 public trials above:

```bash
python3 -BS -m examples.seat_handoffs schedule "$scratch/$fixture"/schedule-*/ --checkpoints
```

The reader scans submissions in native order. It records the first legal answer, the first time both seats have legal answers, and the first time those answers match. A malformed answer clears the seat's previous answer. Each rule uses only the prefix available at the time. If a rule never triggers, the comparison keeps the native final grade. Only **later recorded writes** make a trigger early. They are not a measurement of time, tokens, or money saved.

| Archived two-seat teams | Teams | Reached agreement | Agreement before the final write | Later writes in those teams | Native successes lost by freezing | Native failures turned into successes |
|---|---:|---:|---:|---:|---:|---:|
| Both seats finished | 117 | 49 | 8 | 56 | 0 | 1–3 |
| Also include verified grades after a seat timeout | 171 | 58 | 15 | 428 | 1 | 2–7 |

In the completed group, just 1 of 66 talking teams and 7 of 51 silent teams agreed before the final write. Freezing the *first legal answer* instead lost 6 native successes. Waiting for *both to give a legal answer* without requiring agreement lost 3. The rules test different things. These are retrospective comparisons on fixed records, not interventions on live teams.

The [registered question](samples/schedule-checkpoint-question.json) fixes the population, the 3 rules, and the test. The [aggregate results](samples/schedule-checkpoint-outcomes.json) retain the full 192 scheduled slots. Bounds allow every answer key compatible with the native grades for each exact task checksum.

### What if a supervisor freezes the board when one seat finishes?

**Keep the other seat running unless you have stronger evidence.** One completed team made its first correct submission on write 4 of 4. Under the clock condition below, the first seat had already returned. Freezing the standing answer would lose that native success.

```bash
python3 -BS -m examples.seat_handoffs schedule-jobs "$job_dir" --seat-exit
python3 -BS -m examples.seat_handoffs schedule-jobs "$job_dir" --seat-exit --exit-clock-ms 1000 --include-timeouts --json
```

The trigger is a clean seat return **strictly inside** its peer's recorded execution interval. With no trigger, keep the final answer. Harbor records host timestamps; the stock world records elapsed time. Without an alignment assumption, a return does not locate a board write. The default admits every ordered prefix, including the empty board.

Give `--exit-clock-ms N` only for a fresh stock world that started after the native trial began and froze before verification finished. The operator also assumes that the two clocks disagree on elapsed intervals by at most `N` milliseconds. The reader does not measure that discrepancy. It keeps all prefixes if the source version or host bounds lack support. The next accepted write can bound an earlier append. A write's own timestamp is not an upper bound: the world takes it before the append.

| Archived population | Native successes | Losses without clock alignment | Losses at 1 s | Losses at 5 s |
|---|---:|---:|---:|---:|
| 117 completed teams | 54 | 0–54 | 2–54 | 1–54 |
| 171 teams including verified grades after a seat timeout | 65 | 0–61 | 3–61 | 2–61 |

At 1 s, 34 completed teams certainly had a later write. Only 64 of the 117 had usable elapsed-clock evidence. The upper bounds remain wide. The minimum loss, not the total loss, is identified under the stated condition. A clean return does not prove that a detached process stopped. No agent was interrupted, and the study did not measure time or money saved.

The [registered question](samples/schedule-seat-exit-question.json) fixes the test and clock settings. The [redacted results](samples/schedule-seat-exit-outcomes.json) include coverage, all settings, known-key checks, and the 3 conditional loss witnesses. All 8 job locks remain in the denominator, including the 12 missing directories.

You can now [replay the same real teams from public shards](portable-exit.md) without Docker, private trials or a model key. Try changing the clock allowance or omitting one worker's shard. At 1 second, losing one shard made as many as 4 *remaining* teams' grades less certain.

## Add an instrumented world

Start with the [reference producer](../tasks/revised-dispatch/environment/world.py) and [verifier](../tasks/revised-dispatch/tests/verify.py). The producer declares the possible message/receiver edges at startup. It records only the first send, reply prepared, and optional acknowledgement for each relevant edge. Each receiver can make one irreversible commit. A monotonically increasing `seq` orders the events within one world instance. Use symbolic message IDs, never message bodies or secrets.

Set `schema` to `harbor.handoffs/v1`. Declare `acknowledgements: true` only if the world records them. Emit one JSON object per line; the final newline commits that frame. The verifier must stop world mutations before appending a `closed` frame to its exported copy. Otherwise the absence of an event cannot establish that it never occurred. Validation of a supplied journal does not authenticate its producer.
