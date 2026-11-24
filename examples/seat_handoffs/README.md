# Did the receiver get the report before it committed?

Use this reader after a Harbor team trial. It reports one row per declared message and receiver, not one row per chat message. Compare the handoff to an irreversible decision without guessing from the final score. Run from the repository root with Python 3.12 or later. The reader needs no Harbor installation, credentials, or model calls.

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

## Add an instrumented world

Start with the [reference producer](../tasks/revised-dispatch/environment/world.py) and [verifier](../tasks/revised-dispatch/tests/verify.py). The producer declares the possible message/receiver edges at startup. It records only the first send, reply prepared, and optional acknowledgement for each relevant edge. Each receiver can make one irreversible commit. A monotonically increasing `seq` orders the events within one world instance. Use symbolic message IDs, never message bodies or secrets.

Set `schema` to `harbor.handoffs/v1`. Declare `acknowledgements: true` only if the world records them. Emit one JSON object per line; the final newline commits that frame. The verifier must stop world mutations before appending a `closed` frame to its exported copy. Otherwise the absence of an event cannot establish that it never occurred. Validation of a supplied journal does not authenticate its producer.
