# Observe a native team before its grade exists

A Harbor trial can earn full reward after a supporting seat raises an error or the harness stops it. The team grade cannot tell a workflow whether that seat returned. The optional `--seat-events` flag supplies that signal without changing the task, agents or verifier.

## Read the saved event before the final result

Run from this fork with Python 3.12 or later. No installation, Docker, key or model is needed.

```sh
p=examples/seatlab/results/seat-event-stream
python3.12 -BS -m examples.seatlab.workbench.seat_stream \
  "$p/journals/support-return.prefix.jsonl"
python3.12 -BS -m examples.seatlab.workbench.seat_stream \
  "$p/journals/support-return.prefix.jsonl" --result "$p/results/support-return.json"
```

The first command shows `p2: returned` and `p1: entered_only`. The external host captured those 4 complete rows while the native CLI still ran. No `result.json` existed immediately before or after the copy. The second command adds the later result: both seats returned, but only `p2` had returned **in the prefix**. `entered_only` means no exit was recorded yet. It does not establish that a process was alive.

## Observe your own team

Use a Harbor environment for **this fork and branch**. From the checkout, set `PYTHONPATH="$PWD:$PWD/src"` if you use the supplied participant class. The following example runs two scripted stock Oracle solutions on local Docker. It needs no provider credentials. Use a new trial directory, never an existing receipt.

```sh
p=examples/seatlab/results/seat-event-stream
test ! -e trials/observed-hello-team || exit 1
PYTHONPATH="$PWD:$PWD/src" harbor trial start -c "$p/hello-support-return.yaml" --seat-events
```

In another terminal in the same checkout, inspect `trials/observed-hello-team/seat-events.jsonl`. After the trial finishes, add `--result trials/observed-hello-team/result.json` to the snapshot command. A trial with its own task, participants or models uses the same CLI flag. The flag does not wrap, prompt, interrupt or issue commands to any participant. Without the flag, Harbor does not create this journal. The flag is not available for single-agent trials or `--init`.

For a Python integration, open `with trial.seat_events() as stream:` before `await trial.run()`. Read the async iterator in another task. Leave the context before waiting for the iterator to finish. The `closed` row is written when the context exits, not when the first participant returns. Do not wait for `closed` inside a trial hook. Use the journal for additional independent readers; the in-process stream has one consumer.

## Registered first attempts

[The registration](registration.json) fixes 8 cases before any Docker start. All 8 ran on the native CLI. The 7 opted-in trials produced 7 private mode `0600` journals. All 12 known journal exits match the corresponding final native seat. Two seats never entered play after a setup refusal; neither journal nor native result supplies an exit. No retry replaced a first attempt. These are scripted controls, **not** fresh LLM teams.

| Native control | Tested seat | Other seat | Native reward |
| --- | --- | --- | --- |
| Stock shipping, observer disabled | `returned` | `returned` | 1 |
| Same stock shipping, observer enabled | `returned` | `returned` | 1 |
| Supporting seat returns early | `returned` | `returned` | 1 |
| Supporting seat waits for a harness stop | `returned` | `stop_requested` | 1 |
| Supporting seat cancels itself | `returned` | `cancelled` | 1 |
| Supporting seat raises after its Oracle work | `returned` | `raised` | 1 |
| Both seats wait for the team deadline | `stop_requested` | `stop_requested` | 1 |
| Setup refuses before play | Not observed | Not observed | No valid grade |

Both shipping seats are under test. In `hello-seats` only `p1` is under test. The shipping pair kept the same native task checksum, seat/result field names and six original unscoped trial events. The disabled control produced no new journal. The [shape comparison](stock-result-shapes.json) contains field names, not private values. This is one paired compatibility test, not a performance bound or a guarantee for every task.

During `support-return`, the host clock placed the `p2` end at 6.215 s from stream opening and the `p1` end at 11.200 s. This ordering comes from the native process. It is not a peer message time or a cross-machine clock measurement.

## What the signal does and does not mean

- `open` declares the native trial UUID, the seat names and which seats are under test. There is no prompt, message, model ID, credential or exception text in a journal row.
- `run_enter` precedes `agent.run`. It is not evidence of a model call, message read or tool action.
- `play_end` records the native play disposition. `stop_requested` records the harness request; it does not promise the agent or child processes died. Null is unknown, not a return.
- Sequence numbers are local to one trial. The journal uses elapsed monotonic nanoseconds, not wall time. There are at most `2 × seats + 2` rows. The producer does no file I/O in a seat callback.
- A reader ignores an incomplete final line while the journal is open. Missing events or `closed` never imply a seat passed. The result comparison binds the UUID and complete seat/role roster. Exit 1 means two known values conflict. Exit 2 means malformed or incompatible evidence. Absence alone is not a conflict.
- The writer refuses to replace an existing journal. If a later write fails, it warns and keeps the available prefix; it does not change the native grade. This host file is not a durable messaging service or an authenticated remote log.

### Evidence boundary

`journals/` contains the original JSONL bytes from this study, plus the actual external prefix. `results/` contains **excerpts**, not entire native results. Each excerpt retains the UUID and native seat role/disposition fields. [Observations](observations.json) preserves native trial event names, grade validity, the reward, private file mode and fixed witness labels. For `hello-seats`, the wrappers mark their own actions in the host trial directory, outside all player and verifier mounts. Neither the wrapper nor the monitor reads the other to assign a disposition. Stock shipping used unmodified Oracle participants.

Registration commit: `480b9b4131e1d20d97451ad39201f9f99861eff2`. Native and participant source: `c8a5e69af50044ba191e62addb248f3fc45b428b`. Task Git trees: `hello-seats` `43928b740204e7b74a0794c275d9c188add25a67`; `ship-handover` `c09e2f59e06ac324e5d9f2dbdc74d4da423de08e`. These receipts show lifecycle behavior. They do not measure whether a supervisor should stop a live model team.
