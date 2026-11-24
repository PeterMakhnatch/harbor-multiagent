# Check what changes between two native teams

Before paying for a comparison, name the one prepared input that should differ. `isolate` compares two local task directories. By default it reads two native **trial** locks. Use `--configs` to build the locks in memory from two local trial configs instead. Neither mode starts a seat. Use the fork's installed Python environment. A job lock describes a batch and is not a trial lock.

## Try two controls and one confound

From the fork checkout, use the stock two-seat shipping task. The helper prepares three local trial locks from two [released registrations](../../ship_handover/samples/two-worlds). It checks the original task files and their executable bits. It prints the new task digest and the archived digest separately. A difference is expected on this role-aware branch. The three locks are **new inputs**, not records of past executions. The helper refuses a changed release or an existing output directory.

```sh
py=.venv/bin/python
wb=examples.seatlab.workbench
t=examples/tasks/ship-handover
work=$(mktemp -d)
locks="$work/locks"
"$py" -B -m examples.ship_handover.arm_inputs "$locks"
```

| Left lock | Right lock | Declared change | Expected |
| --- | --- | --- | --- |
| `recovery-deliver` | `sender-only-deliver` | Participant `relief` | Exit `0`: only its participant configuration differs. |
| `recovery-deliver` | `recovery-withhold` | World setting `SH_CHANNEL` | Exit `0`: only that input differs. |
| `sender-only-deliver` | `recovery-withhold` | World setting `SH_CHANNEL` | Exit `1`: the relief participant also differs. |

```sh
"$py" -B -m "$wb" isolate "$t" "$locks/recovery-deliver.json" "$t" "$locks/sender-only-deliver.json" --participant relief
"$py" -B -m "$wb" isolate "$t" "$locks/recovery-deliver.json" "$t" "$locks/recovery-withhold.json" --world-setting SH_CHANNEL --json
```

For your own task, replace each directory and lock. Use `--role-instruction SEAT` for the native instruction a seat loads. A line-ending change alone does not qualify. Use `--participant SEAT` for a harness or participant configuration, not for evidence about the served model. Use `--world-setting NAME` for one explicitly set environment key, not for proof of delivery.

Give the second worker the `comparison` value from the JSON report. Add `--expected-comparison sha256:…` when checking the same ordered pair. Reversing the arms, renaming the target, or changing a checked input produces a different value. Keep the inputs private. The output omits paths, role texts, participant settings and environment values. The digest is an input check, not a signature.

## Check trial configs before any worker starts

Use the [shipping benchmark's preparation commands](../decision_receipt/README.md#prepare-and-check-the-four-native-controls) to create a task and four native JSON configs. Stop after preparation. With those commands' `work` and `py` variables, compare two planned controls:

```sh
t="$work/task"
c="$work/controls/configs"
"$py" -B -m examples.seatlab.workbench isolate --configs \
  "$t" "$c/dr-settles-plain.json" "$t" "$c/dr-settles-cited.json" \
  --participant relief --json
```

Exit `0` means only the receiver configuration differs in this pair. Now substitute `dr-fails-cited.json` on the right. Exit `1` means the pair does **not** isolate the receiver: the supplier fate also changes. Neither result measures a decision, a citation, or a reward.

For a prompt comparison, keep the participant configurations and world settings identical. Use two copies of the task with the same directory name and different parent directories. Change one seat's instruction in one copy. Set each config's `task.path` to its own absolute task directory. Compare with `--role-instruction SEAT`. Use the resulting `comparison` digest to share the prepared inputs. Use `trial_lock_records` only to compare with actual registration records later.

Config mode accepts a plain local JSON trial config, not a job, YAML, or a fully general Harbor invocation. Use an explicit local `task.path`, the participant roster and optional Docker world settings. Configs that depend on external skill files, compose overlays, or a source trial do not qualify. No participant adapter is imported or run. Trial names and output paths do not identify a treatment. The report describes inputs under this checkout's native lock builder; it is not evidence that a worker used them.

## Interpret the report

- **Exit `0`:** the declared input differs, and no other checked input differs. This is not permission to spend.
- **Exit `1`:** the two readable inputs do not establish the declared comparison. Inspect `reasons` and `other_changes`.
- **Exit `2`:** the tool cannot bind a supplied lock, source tree or roster. There is no comparison.

The checker uses both the raw role file and Harbor's loaded text. It checks the native package digest, participant order, other lock fields, non-role file contents, and which files are executable. It reads unshipped local files too. A moved local task can qualify if its directory name, checked inputs and lock still agree. Cosmetic or external path changes can cause a refusal. Use stationary local inputs. This is not a filesystem snapshot or a deployment attestation.

This checkout includes the [role-aware native packager](https://github.com/PeterMakhnatch/harbor-multiagent/pull/134). Each declared seat instruction is in a new package and its digest. The legacy packager omitted those files. Two different instructions could then have the same native digest. `isolate` refuses an instruction-only claim under such locks. It can compare participant or world inputs in matching local trees and reports `native_lock_covers_roles: false` if the installed packager omits the roles. Keep the original locks with old results. A fresh lock is not evidence about an old trial.

This tool does not compare host gates, scheduling, external files behind a path, mutable images, provider behavior or messages actually received. It does not compare job-level retries or sample size. Use the [handoff author guide](../../seat_handoffs/author.md) to check what a benchmark observes. Use the [budget preview](team-account.md#decide-whether-a-proposed-run-fits-a-budget) to price an authorized plan separately.

## Check which inputs reached the retained final results

After a run, give `cohort` a stationary native job directory, not a trial directory. The job needs a v3 job lock and local v2 multi-seat trial locks. Do not edit an old lock to make it match. If possible, save the raw job lock digest when you register the job. A digest calculated today cannot prove what a worker registered yesterday.

```sh
"$py" -B -m "$wb" cohort "$job" --expected-job-lock "$job_lock_pin"
```

Use `cohort --pair` only if **one job** can register both inputs. Harbor uses one participant roster and one environment for every trial in a job. One job can compare different task directories, for example two versions of a receiver instruction. It cannot compare two rosters or two values of `SH_CHANNEL`. For those comparisons, use [two worker jobs](#check-a-participant-or-channel-comparison-across-two-worker-jobs).

For a two-input job, take `trial_lock_records.left` and `.right` from the successful preflight. These keys include the literal task locator. They differ from the portable `comparison` pin. Pass the planned keys rather than choosing two successful groups after the fact:

```sh
"$py" -B -m "$wb" cohort "$job" --expected-job-lock "$job_lock_pin" --pair "$left_key" "$right_key" --json
```

The registration is a **multiset** of full input records. It contains no slot names or execution IDs. The report counts repeated copies. It checks the final result against its own job, slot and execution ID. It then checks the neighboring trial lock against the registration. Finally, the result must agree on the local task path, ordered participants, and recorded world settings. Two omitted settings declarations agree as omissions. An omitted declaration does not match an empty or nonempty explicit mapping.

- Exit `0`: the closed job has the expected final input coverage. This does not require a positive grade.
- Exit `1`: the records conflict, or the job does not register exactly the requested pair.
- Exit `3`: some evidence is missing or cannot separate the full input records. `grades: null` means withheld, not zero.
- Exit `2`: the reader cannot safely inspect the job or the enrollment pin differs. No report is available.

A file next to a grade is not a signature. Different full locks can have the same visible result inputs, for example if only their timeout differs. The checker withholds the grades for those groups. It also withholds all grades for an overrepresented group instead of choosing winners from it. Counts concern retained final directories, not all retries. The report does not prove that the promised task bytes ran or that a seat received a message.

**Check on an existing job:** 20 saved three-seat finals matched 10 distinct full input records, with 2 copies of each. The results reported 2 valid ones and 18 valid zeros. All 10 used one participant configuration across 10 tasks. Nominating two of those records as the whole comparison returned `outside_pair`. This job does not compare two models.

### Capture which inputs started

Use this when a registered difference is absent from the final result's comparable inputs. Before a future authorized job, create a separate owner-only directory on the trusted host. Keep it outside the job and outside all seat mounts. Use a separate directory from END, seat, and billing receipts.

```sh
mkdir -m 700 "$start_archive"
```

Add the following arguments to the job configuration. Use an absolute, resolved path. This guide does not start a job.

```text
--plugin examples.seatlab.workbench.input_keeper:InputStartKeeper --plugin-kwarg archive_dir=/absolute/private/starts
```

At the native START event, the plugin checks the job, slot, execution ID, saved lock and native inputs. It stores their keys and a digest of the full start configuration, local task ID and task checksum. It does not record prompts, settings values, rewards or seat messages. It writes once per execution. A failed capture warns and does not stop play. An END event cannot fill the gap.

```sh
"$py" -B -m "$wb" cohort "$job" --expected-job-lock "$job_lock_pin" \
  --pair "$left_key" "$right_key" --starts "$start_archive"
```

With `--starts`, every retained final needs its own matching receipt. Two full locks that have the same result view can now be separated. If the final lacks its receipt, its grade stays unattributed. A conflicting receipt withholds all grades for that lock. An extra START without a final counts separately. It is not a grade, failure or proof of a retry.

This check trusts the host and its private archive. The saved evidence is not a signature. START proves neither the eventual deployment nor the content of a seat's context. On the retained 20-final job above, requiring START receipts withheld all 20 grades: no such archive was available. Do not create receipts from final results.

## Check a participant or channel comparison across two worker jobs

Before either worker starts, keep the ordered `trial_lock_records` from `isolate` and choose the number of attempts **per arm**. The jobs must use the same task locators that the preflight used. If work moves to different absolute paths, recheck the inputs at their final locations. A portable comparison digest is not a substitute for a native registration key.

Give each worker its own native job config. Set `tasks` to its one local task, `participants` to its roster, and `environment` to its world. Set `n_attempts` to the chosen number in both configs. Leave `agents` at its native default. Do not combine the two configs or use a dataset that adds a different source locator. These are ordinary `harbor run` jobs, not standalone `harbor trial start` trials. The shipping benchmark's four calibration controls remain standalone.

Check these exact job config files **before assigning a worker**:

```sh
"$py" -B -m "$wb" cohort-jobs "$left_config" "$right_config" \
  --plans --pair "$left_key" "$right_key" --copies "$copies" --json
```

`matches_plan` (exit `0`) means Harbor expands each config to the expected input and number of copies. The requested concurrency and retry limits must match. `different_plan` (exit `1`) explains what differs. Unsupported inputs return exit `2`. This check supports 1–64 copies per job, one local task and an explicit team. It registers no job, reads no provider prices, and predicts no reward.

At registration, keep the digest of each worker's actual `lock.json` separately. A job lock includes its creation time. You cannot derive these two file digests from `isolate`. After both jobs stop changing, use the held digests and the originally chosen count:

```sh
"$py" -B -m "$wb" cohort-jobs "$left_job" "$right_job" \
  --pair "$left_key" "$right_key" --copies "$copies" \
  --job-locks "$left_job_pin" "$right_job_pin" --json
```

`covered` (exit `0`) means two different jobs registered the assigned inputs and all expected final results support attribution. It does not require a positive grade. `inconsistent` (exit `1`) withholds both grade tallies. Examples include swapped arms, the wrong repeat count, or a duplicated execution across the jobs. `incomplete` (exit `3`) means some evidence is missing. A missing final is never a zero. A wrong file digest or unreadable job returns exit `2` and no partial result.

Use `--starts "$left_starts" "$right_starts"` to require private host START receipts for both jobs. Separate job directories do not resolve two inputs that have the same visible result fields. Use the [morning report](team-account.md#check-finished-jobs-from-several-workers) if a worker has not produced a readable job. The two-job report checks registered inputs and retained final results. It does not measure what a seat saw or establish a treatment effect.

**Check on saved jobs:** In one earlier sweep, 8 native jobs each used the same three-seat task. We chose two by roster, before reading their grades. Only the reviewer changed, from a scripted no-op to a scripted oracle. Both jobs supplied one distinct final with a valid zero. Swapping the expected inputs, asking for two copies, or supplying one job twice withheld both tallies. We calculated the file digests from the saved locks, not at original enrollment. This checks the reader on real job directories; it is not a preregistered comparison or a model result.
