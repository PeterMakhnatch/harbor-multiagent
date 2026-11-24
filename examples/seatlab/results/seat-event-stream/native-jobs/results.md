# Keep seat evidence for a real Harbor job

Use the plugin when another workflow needs to know which participant finished. It adds a host-side file to each new local multi-seat attempt. It does not inspect messages, change a verifier, start a second trial, or call a model. Configure jobs as usual. Select the plugin in the command, not in `JobConfig`.

## Run or inspect a batch

From the fork checkout with the matching Harbor environment, run the supplied two-attempt job in a fresh directory. The two participants run scripted Oracle solutions, not models. No provider key is needed.

```sh
p=examples/seatlab/results/seat-event-stream/native-jobs
plugin=harbor.cli.seat_events:SeatEventsJobPlugin
test ! -e jobs/observed-hello-team || exit 1
PYTHONPATH="$PWD:$PWD/src" harbor run -c "$p/observed-job.yaml" --plugin "$plugin"
python3.12 -BS -m examples.seatlab.workbench.job_stream jobs/observed-hello-team
```

To inspect evidence without Harbor or Docker, run the same reader on `"$p/job-return"`. Add `--json` for machine output. To inspect one trial while it runs, use [the journal reader or external wait](../results.md). The batch reader is for stationary, trusted local multi-seat job directories, not a live watch. Do not upload full job folders to share these reports: they can contain sensitive settings. The bundled excerpts omit prompts, settings, local paths and trajectories.

The reader takes the enrollment count and job ID from the root result. It binds a child by its native job ID, trial name, UUID and roster before comparing seat ends. Exit 0 means evidence coverage, **not** a successful grade or a normal return. A closed journal can correctly report a stop. A missing final result stays in the denominator. An unbound journal cannot fill that slot. Exit 1 means incomplete or conflicting evidence; exit 2 means the input is invalid. Earlier retries do not become extra logical slots.

## What happened in the first attempts

[Registration](registration.json) fixed 4 logical trials in 3 jobs, with retries disabled. Every world earned 1. Two jobs ran in parallel; the two attempts within `job-return` ran sequentially, as registered. The two observed jobs produced 3 private mode `0600` journals. All 6 named seat exits agreed with their native result. The default job produced none.

| Native job | Logical trials | Journals | What the host saw on `p2` before a native trial result existed |
| --- | ---: | ---: | --- |
| `job-default` | 1 | 0 | No observation. The final result records a return. |
| `job-return` | 2 | 2 | A return in each. The peer had entered and had no recorded end. |
| `job-stopped` | 1 | 1 | A harness stop. The peer had returned. |

The 3 live receipts came from a separate process while the native job CLI remained active. Each is a strict byte prefix of its final journal. For a concrete example, compare a [live return prefix](prefixes/e3b8fdea-d2fa-4940-87a1-2ff655c2d98f.jsonl) with its later native seat result. The [receipt index](native-receipts.json) lists the other two prefixes, the trial IDs, grades and file checks.

## Resume and retry boundaries

`harbor job resume --job-path JOB_DIR --plugin "$plugin"` enables observation only for attempts that actually start. Resuming the completed default job with the plugin did not create a journal. Resuming the completed observed job **without** the plugin kept both journals. Both commands exited 0. All 3 earlier result files kept their bytes, UUIDs and modification times. Both observed journals kept their bytes and modification times. No new trial directory appeared.

The ordinary job retry policy can delete the earlier trial directory. A journal from the surviving directory is evidence about that execution only. A `closed` event follows the native end hooks: an uploader listening only for `END` can capture a prefix. No retry, partial restart, remote host, or model was part of this four-trial calibration.

The final reader produced identical report bytes on the full native jobs and on the public excerpts. With one trial directory withheld, the operator still saw 2 enrolled slots, 1 bound result and 1 missing result. With only its result file withheld, the operator also saw 1 unbound journal; the enrollment stayed 2.
