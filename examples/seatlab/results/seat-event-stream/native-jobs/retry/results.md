# Keep execution history when Harbor retries a team

A job slot and an execution are different. Harbor can retry one slot under the same trial-directory name but with a fresh execution UUID. The default job folder stores the last execution. The local keeper retains the earlier seat journal without keeping prompts, trajectories, whole job configs or private world state. A journal is not a grade.

## Run the current collector

Use a new private archive **outside** the job directory on the same filesystem. Supply only this plugin; it already includes the batch journal writer. This example runs scripted native Oracles and does not call a model.

```sh
p=examples/seatlab/results/seat-event-stream/native-jobs/retry
archive=$(mktemp -d "$PWD/.seat-attempts.XXXXXXXX")
chmod 700 "$archive"
test ! -e jobs/observed-retry-team || exit 1
PYTHONPATH="$PWD:$PWD/src" harbor run -c "$p/retry-job.yaml" \
  --plugin examples.seatlab.workbench.attempt_keeper:AttemptKeeper \
  --plugin-kwarg "archive_dir=$archive"
python3.12 -BS -m examples.seatlab.workbench.attempt_history \
  jobs/observed-retry-team/result.json "$archive"
```

The hook makes **one hard link** at native `START`. The operator and trial paths then refer to the same private mode `0600` file. The native writer still writes each row once. Removing the trial path on retry does not remove the operator's link. A collision never overwrites a file. A runtime link failure warns without changing the native retry or grade. A private archive on another device, a nonprivate directory, or a directory nested with the job is not supported. A hard link is not a backup against file corruption, truncation or disk loss. Never edit the linked files. Restrict the archive to one trusted operator and one job.

## What the first comparison found

The [first registration](registration.json) enrolled one logical slot in each of two independent jobs. In each job the supporting seat raised after its Oracle work. Native Harbor retried once, on the same directory name but with a different UUID. Both final verifiers assigned 1. Each final result recorded `p1: returned` and `p2: raised`.

A separate process saw both UUIDs in each job. For each earlier execution it captured 5 rows before Harbor removed the ordinary path. Only in the retention arm could we check what the poll missed: the archive kept a sixth `closed` row for that execution and all 6 rows for its successor. Its final journal matched the surviving native file byte for byte. The first execution has no retained native grade here. Counting these as two graded trials would be wrong. [The firsts](firsts.json) identify all four executions. The [earlier flat-format archive](unindexed-archive/) is pinned evidence from the first collector, not the input format of the current history reader.

## Why a journal UUID is not a logical slot

The current collector labels each private filename with the job UUID, a SHA-256 digest of the native trial-directory name, and the execution UUID. The native header independently contains the execution UUID and named seat roster. The job and slot labels come from the trusted collector, not from a signature or the journal header. A slot digest is not encryption. An operator who knows the name can reproduce it.

[The second registration](indexed-registration.json) fixed a new two-slot job. Both slots used identical participants and both retried once. Harbor reported 2 enrolled slots and 2 retries. All 4 private journals were closed and had distinct native UUIDs. An independent observer grouped the actual native directory paths before hashing their names. Both pairs matched the collector. The [two surviving native results](indexed/final/) each gave reward 1. Neither discarded execution has a retained verifier grade. [The witness](indexed/host-witness.json) records which UUID was final for each slot, separately from the archive labels.

Try the full or incomplete history from a public checkout. No install, model or container is needed.

```sh
p=examples/seatlab/results/seat-event-stream/native-jobs/retry/indexed
python3.12 -BS -m examples.seatlab.workbench.attempt_history \
  "$p/job-result.json" "$p/archive"
subset=$(mktemp -d)
for entry in "$p"/archive/*.jsonl; do
  case "$entry" in *.17dc6f76-ba03-4b13-bfc2-1c7e7d638a74.jsonl) continue ;; esac
  cp "$entry" "$subset/"
done
python3.12 -BS -m examples.seatlab.workbench.attempt_history \
  "$p/job-result.json" "$subset"
```

The first command finds 4 of 4 executions across 2 slots. The subset command exits 1 with 3 observed and 1 missing. It does not invent a fourth outcome. [Complete report](indexed/complete.json) · [withheld report](indexed/withheld.json). A running job has no final expected-execution count. A null end, incomplete header or open journal cannot establish a known final seat outcome. Invalid identity or malformed files give exit 2. Reading a public copy does not require access to the original private directory.

**Coverage is not provenance.** In a separate offline check, changing one slot label regrouped four genuine journals from 2+2 to 3+1. The count still passed. The independent host witness detected the wrong grouping. Keep the collector directory private, or transmit a digest through a trusted channel. UUID order is not time order. A complete journal is not evidence of a correct task answer. These comparisons used scripted seats, local storage and no paid model calls.

## Check an independent observer

Add `--witness "$p/host-witness.json"` to the history command. This compares execution UUIDs and slot assignments, not grades. The [real witness report](indexed/witnessed.json) finds 4 matches and no moved execution. For a control, use `--witness "$p/challenge-witness.json"`. This file deliberately assigns one of the same four UUIDs to the wrong slot. The [control report](indexed/misattributed.json) keeps `coverage_complete: true` but reports 3 matches, 1 moved, and exit 1. A witness from another job gives exit 2. Neither a file digest nor this comparison proves that two files came from independent observers.
