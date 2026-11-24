# Check what changes between two native teams

Before paying for a comparison, name the one prepared input that should differ. `isolate` compares two local task directories and two native **trial** locks. It starts no team and reads no API key. Use the fork's installed Python environment. A job lock describes a batch and is not a trial lock.

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

To check a particular two-input job, take `trial_lock_records.left` and `.right` from the successful preflight JSON. These keys include the literal source locator. They differ from the portable `comparison` pin. Pass the original keys rather than choosing two successful groups after the fact:

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
