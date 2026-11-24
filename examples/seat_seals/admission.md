# Admit each worker ticket once

[`workers run-next`](workers.md) stops a second start inside one worker directory. It cannot see a second directory. This operator adds one coordinator with one private ledger. A worker asks the coordinator once for one assigned ticket. It starts the native team only after a fresh grant. Every ticket is granted at most once, forever.

## Read the partial result first

From the root of this fork, with Python 3.12 or later:

```bash
p=examples/seat_seals/samples/admission
python3 -BS -m examples.seat_seals.workers show "$p/plan.json" "$p/worker-0.json" "$p/worker-1.json" \
  --expected-plan sha256:d09f25377e56ca3c0d8a85d29141701272e6567dd1e974bc5dd6ac978c0359fc \
  --admission-status "$p/status.json"
python3 -BS -m examples.seat_seals.qualify_admission "$p/plan.json"
```

`show` reads 2 of 3 worker packets and the coordinator status. It reports 3 of 18 tickets issued: 2 with a valid grade and 1 unknown. Overall, 16 of 18 grades are unknown, including the 15 unissued tickets. `qualify_admission` runs the wire and ledger probes on a temporary ledger. It starts no native trial and needs no Docker.

## Native result

We [froze the question](samples/admission/question.json) first. A scripted qualifier then ran on one Mac with one Docker engine. The workers were separate processes with separate directories, not separate hosts. No SSH link was used. No model ran.

| Unit | What happened |
|---|---|
| Old path: two stock `run-next` starts of one ticket in two directories | Both first attempts started and graded 0. The joined collection quarantined the ticket. |
| Race: two admitted workers ask for one ticket at the same time | One grant and one denial. One native start, graded 0. |
| Lost grant: the reply is discarded after the ledger records it | No native start. A fresh directory asked again and was denied. The ticket stays issued; its grade is unknown. |
| A different worker, next ticket | One grant and one native start, graded 1. |

The old-path attempts are a separate comparison. They never fill an admitted slot. See the [native outcomes](samples/admission/native-outcomes.json) and the [qualifier report](samples/admission/qualifier.json).

A separate check added a non-admitted legacy packet for worker 2, outside the registered primary cohort. `show` reported 1 unissued ticket with a grade. The grade stayed valid.

## Run it

Use the [worker plan](workers.md#assign-and-acquire) and its digest from a trusted route. Keep `study.json` private.

1. On the coordinator, create the ledger once, on a local disk. The ledger directory must not exist. It is created with mode `0700`.

```bash
python3 -BS -m examples.seat_seals.admission init "$work/plan.json" "$ledger" --expected-plan "$plan_digest"
```

2. On each worker, write the `--via` executable. It receives one request on stdin and must print one reply on stdout. The worker starts it with no arguments, in `/`, with only `PATH`, `HOME`, `USER`, `LOGNAME`, `LANG`, `LC_ALL`, `LC_CTYPE`, `TMPDIR` and `SSH_AUTH_SOCK`. It must be an absolute regular file, executable, owned by you or root, and not writable by group or others. The exchange stops after 60 seconds.

One box: set `py` to an absolute Python 3.12 or later, and `fork` and `ledger` to absolute paths. The file pins all three. It refuses to replace an existing file.

```bash
py=/absolute/path/to/python3.12
via="$HOME/bin/seat-admit"
mkdir -p "$HOME/bin"
(set -C; cat > "$via" && chmod 700 "$via") <<EOF
#!/bin/sh
cd "$fork" || exit 2
exec "$py" -BS -m examples.seat_seals.admission serve-once "$ledger"
EOF
```

SSH: use a dedicated key with a forced command on the coordinator. Replace `/abs/python3.12` with the coordinator's absolute Python 3.12 or later. The command takes no arguments from the client and no credentials in the request. `restrict` turns off agent, port and X11 forwarding.

```text
# coordinator: ~admit/.ssh/authorized_keys
command="cd /srv/fork && exec /abs/python3.12 -BS -m examples.seat_seals.admission serve-once /srv/admission/ledger",restrict ssh-ed25519 AAAA... seat-admit
```

```sh
#!/bin/sh
exec /usr/bin/ssh -T -a -x -o BatchMode=yes -o IdentitiesOnly=yes -i /home/w/.ssh/seat-admit admit@coordinator
```

On the worker, save this as `$via` with mode `700`, in place of the one-box file. The SSH route was not part of the native result.

3. Check the ledger at any time:

```bash
python3 -BS -m examples.seat_seals.admission status "$ledger"
python3 -BS -m examples.seat_seals.admission status "$ledger" --json > "$work/status.json"
```

4. Give each worker its own private directory. Choose an assigned ticket that `workers status` shows as `absent`. Run one admitted attempt. It needs the fork installed and a running Docker engine.

```bash
root="$work/worker-$id"
mkdir -m 700 "$root"
python3 -BS -m examples.seat_seals.workers status "$work/study.json" "$work/plan.json" "$root" --worker "$id"
python -B -m examples.seat_seals.admitted_run "$work/study.json" "$work/plan.json" "$work/shift-board-seal" "$root" \
  --worker "$id" --ticket "$ticket" --expected-plan "$plan_digest" --via "$via"
```

It asks once, never retries, and prints one JSON report.

| Exit | Status | Meaning |
|---:|---|---|
| 0 | `completed` | Fresh grant; the native runner finished cleanly. |
| 1 | `native_failed` | Fresh grant; the native attempt failed. Do not rerun it. |
| 2 | `refused`, `admission_failed`, `blocked_after_grant` | Input or transport failed, or the start failed after the grant. After a grant, the ticket is spent. |
| 3 | `already_issued`, `occupied` | The ticket was already granted or its entry exists. |

5. Stop the workers. Pack each one and show the packets with the coordinator status:

```bash
python3 -BS -m examples.seat_seals.workers pack "$work/study.json" "$work/plan.json" "$work/worker-$id.json" "$root" --worker "$id"
python3 -BS -m examples.seat_seals.workers show "$work/plan.json" "$work/worker-0.json" "$work/worker-1.json" \
  --expected-plan "$plan_digest" --admission-status "$work/status.json"
```

## Limits

- A lost grant spends the ticket. Its grade stays unknown. No command grants it again or releases it.
- A grant allows one start. It does not promise one completed trial.
- The coordinator checks the plan, the ticket and the stated worker number. It does not authenticate the worker. Anyone who can run `--via` can ask for any worker's ticket.
- Run one coordinator on one local, durable disk. Never restore an active ledger from a snapshot or backup. Never run two replicas. Do not put the ledger on NFS or other network storage: SQLite locks are not reliable there. A restored or copied ledger can grant a spent ticket again.
- The old `run-next` and `run` commands do not ask the coordinator. Nothing here prevents them. `unissued` does not prove that no other launcher ran. An unissued ticket with a grade is reported as a disagreement and keeps its grade.
