# Run a paired native team study on independent workers

Use this operator when workers do not finish together. It assigns both policies before play, starts each ticket at most once in its assigned trials directory, and keeps missing workers in the report. A recipient needs this checkout and Python 3.12+, not the private registration, task package, Docker, or credentials.

## Try the recipient first

From the root of this fork:

```bash
p=examples/seat_seals/samples/workers/paired
python3 -BS -m examples.seat_seals.workers show "$p/plan.json" "$p/worker-0.json" "$p/worker-1.json"
```

This returns 12 known grades, 6 complete interventions, and a total reward difference between −2 and +4 across all 9 pairs. Add `"$p/worker-2.json"` to recover all 9 pairs and the exact difference of 0. Use `--json` to inspect each block, the missing slots, and the attainable counts of positive and negative pairs. Zero packets is also valid.

## Assign and acquire

1. [Materialize and register the study](README.md#run-the-study). Keep the original registration, roster, and task tree private. Do not give the assignments to the model seats.
2. Choose the layout **before** dispatch. Run this once:

```bash
python3 -BS -m examples.seat_seals.workers plan "$work/study.json" "$work/plan.json" --workers 3 --layout paired
```

`paired` keeps both arms of a block on the same worker. `crossed` places the second arm on the next worker, cyclically. Neither layout consults outcomes. The transfer plan contains opaque tickets, block membership, arm labels and digests. It contains no fixture names, seeds, roster, or provider keys. Send the pinned plan digest by a trusted route.

3. Assign **one persistent directory to each logical worker**. Several cooperating processes can serve the same worker on that directory. Each invocation starts at most one fresh native trial. Use Python with the fork installed and a running Docker engine for `run-next`.

```bash
id=0
root="$work/worker-$id"
mkdir "$root"
python3 -BS -m examples.seat_seals.workers status "$work/study.json" "$work/plan.json" "$root" --worker "$id"
python -B -m examples.seat_seals.workers run-next "$work/study.json" "$work/plan.json" "$work/shift-board-seal" "$root" --worker "$id"
```

`run-next` validates the study and task. It atomically binds the directory to the plan and worker before it starts a ticket. A competing owner cannot adopt it. An occupied ticket, partial trial, or failed attempt does not become available again. Starting a new directory on another machine is **not** a resume. If a disk is lost, record the missing worker; do not reissue its tickets. This is not distributed consensus or a guarantee against a dishonest worker.

| Exit | Meaning | Operator action |
|---:|---|---|
| 0 | The native runner reports a clean attempt and a valid reward. | Record the receipt. Request the next ticket if authorized. |
| 1 | The attempt failed or its runner did not finish cleanly. | Inspect the original directory. Do not rerun the ticket. |
| 2 | The request, task, or ownership check failed. | Correct the configuration before making a new request. |
| 3 | No fresh ticket remains on this worker. | Stop dispatch and export its evidence. |

For a named team, `run-next` also requires `--allow-named-seats`. Set the provider's spending limit yourself. No command here calls a model by default. For remote or automated dispatch, supply `--expected-plan DIGEST` from the coordinator. Set the concurrency limit in the host scheduler.

4. Stop writers before export. Each worker returns a packet and its canonical content digest:

```bash
python3 -BS -m examples.seat_seals.workers pack "$work/study.json" "$work/plan.json" "$work/worker-$id.json" "$root" --worker "$id"
```

A packet contains verified binary rewards or explicit unknowns, return-link and schedule evidence, and scoped identity tags. It contains no native UUIDs, transcripts, environment values or answer keys. Identity tags reveal equality within the study. Conflicting IDs quarantine every affected ticket, including conflicts supplied by a partial attempt. Unbound roots can be partitioned retrospectively for analysis; that does not establish which worker originally ran them. The reader refuses mixed owners, repeated roots, and a changed source during export. Use stationary evidence: it does not acquire a filesystem snapshot.

5. Combine whatever packets have arrived. Use a separately supplied digest when checking a transfer:

```bash
python3 -BS -m examples.seat_seals.workers show "$work/plan.json" --expected-plan "$plan_digest" --checked "$work/worker-0.json" "$packet_digest"
```

A digest detects a change against a value you already trust. It does not authenticate the worker or prove it returned all its evidence. A delivered packet with a missing ticket differs from a worker that has sent no packet.

## What did pairing preserve?

We [specified the question](samples/worker-question.json) before reading the board-seal outcomes. We repartitioned the **same 18 native first attempts** under both layouts and replayed all 8 delivery subsets per layout. We did not run the experiment twice.

| Worker not received | Paired: complete interventions | Paired: possible total | Crossed: complete interventions | Crossed: possible total |
|---|---:|---|---:|---|
| 0 | 6 | [−4, +2] | 3 | [−1, +5] |
| 1 | 6 | [−3, +3] | 3 | [−4, +2] |
| 2 | 6 | [−2, +4] | 3 | [−4, +2] |

Pairing doubled the number of complete comparisons with one worker missing. It **did not narrow the total interval**: both layouts lacked 6 binary grades. Totals are sums of `seal − continue` for the 9 registered pairs. They are not confidence intervals or estimates for model teams. Worker differences and selective loss can bias a complete-case mean.

Removing a host journal retained all 18 final grades but left 8 supported interventions. Damaging only a gate retained 9 interventions but just 8 supported schedules. A conflicting ID from another worker's partial ticket removed a seemingly healthy grade. The separate live dispatch check started 4 fresh scripted native trials. None contributes to the 18-arm effect. No model ran; charges were $0.

See the [packets, all intervals, and integration results](samples/workers/outcomes.json). Run `python3 -BS -m examples.seat_seals.qualify_workers` for the independent finite-outcome and transfer controls.
