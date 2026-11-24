# Wait for one named seat outside Harbor

A workflow that only accepts a normal return must not fire because the team has a grade. Give the waiter the trial UUID, target seat and a deadline. It reads one host file. It cannot contact or stop a participant, launch another command, or charge a provider.

```sh
p=examples/seatlab/results/seat-event-stream/return-gate
python3.12 -BS -m examples.seatlab.workbench.wait_return \
  "$p/gate-return.prefix.jsonl" --trial df38acfa-27d5-46c3-989d-07c5dce20047 \
  --seat p2 --timeout 0
python3.12 -BS -m examples.seatlab.workbench.wait_return \
  "$p/gate-stopped.prefix.jsonl" --trial 68805ba8-34aa-44e9-8233-e5c469dd5401 \
  --seat p2 --timeout 0
```

The first command exits 0. The second exits 1 and prints `not_returned` with `stop_requested`. Both native teams earned 1. A single snapshot with `--timeout 0` reproduces the exact outcome the external process reported during play. For a live wait, give the process an owner-supplied trial UUID and a positive timeout of up to 300 seconds. On a fresh, private, operator-owned trial directory, the owner can read the UUID from the complete `open` header. Do not treat an ID received from a stranger as authentication.

| Registered native first | What the waiter saw on `p2` | What the peer journal showed | Final grade file at notice | Exit |
| --- | --- | --- | --- | --- |
| `gate-return` | `returned` | Entered; no end observed | Absent | 0 |
| `gate-stopped` | `stop_requested` | Returned | Absent | 1 |

The waiter started after the trial's new header appeared in a separate process. Harbor still ran when both waits ended. The later seat results and independent `return`/`hold` control markers agreed with both waits. A verifier's grade of 1 did not distinguish these cases. These are two scripted firsts under the [registered test](registration.json), not rates for real model teams. There were no models, retries or task interventions. Source `baad86c2834bd488c1c91b1cbd232aa16616c4ab`; [receipts](outcomes.json).

Exit 0 requires `run_enter` and an explicit `returned` end for the **named** seat. Other ends, a closed stream with no exit, missing evidence and timeout give exit 1. Use `--json` to distinguish them. Invalid input, unknown seat, changed trial, observed file replacement, truncation or a modified prefix give exit 2. The waiter does not open a FIFO or follow a journal symlink. Polling cannot detect changes that occurred and reverted between samples. The timeout limits new polls; it is not a hard storage-operation deadline. The snapshot reader and the waiter need no network or Harbor install.

To inspect a result from the team as a whole, use the [native journal guide](../results.md). Do not interpret a returned seat as a license to stop its peer.
