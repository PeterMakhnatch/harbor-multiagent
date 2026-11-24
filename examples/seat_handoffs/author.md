# Can this benchmark measure the handoff before we buy model calls?

Choose one sender, message, receiver and irreversible decision. First check whether the saved controls distinguish the message arriving before that decision from a late or absent message. A team reward alone cannot answer this question.

## Check two existing worlds

Run from the root of [Peter's fork](https://github.com/PeterMakhnatch/harbor-multiagent/tree/seatlab/team-operator-workbench) with Python 3.12 or later. These commands use saved files. They do not launch agents, containers or a verifier service.

```sh
p=examples/seat_handoffs/samples
python3.12 -BS -m examples.seat_handoffs.qualify examples/tasks/revised-dispatch \
  --edge reporter revision booker --boundary acknowledged \
  --journal "$p/confirm-late.jsonl" "$p/confirm-stale.jsonl"

s=examples/ship_inflight/samples
python3.12 -BS -m examples.seat_handoffs.inflight_controls \
  "$s/question.json" "$s/public-cuts.json" --boundary returned
python3.12 -BS -m examples.seat_handoffs.inflight_controls \
  "$s/question.json" "$s/public-cuts.json" --boundary acknowledged
```

| Saved control source | What the operator can establish | Exit |
| --- | --- | --- |
| Revised Dispatch, revision acknowledgement | One receiver acknowledged after committing. The other did not acknowledge. Neither supplies the required **before** control. | `1` |
| Ship Inflight, world returned a reference | In 8 cuts the world prepared a reference before relief committed. In 6 it did not return a reference and relief committed. Another 2 cuts have no commitment. Both required controls exist. | `0` |
| The same 16 shipping cuts, receiver acknowledgement | This world did not record receiver acknowledgements. An accepted relief action is not a receipt. | `1` |

The shipping controls used scripted teams, not models. Their public identifiers are tickets, not native execution UUIDs. The adapter reads their [frozen business-world cuts](../ship_inflight/samples/public-cuts.json). It does not construct substitute handoff journals. Neither command estimates whether messages *improve* rewards. Add `--json` to inspect each control. Exit `2` rejects malformed input rather than treating it as a negative control.

## Instrument a new native task

1. Name the edge using the actual `[agents.<seat>]` names in `task.toml`. Use a fixed symbol such as `revision`. Never use a message body, token or customer identifier.
2. Give the same author kit to the world builder and the verifier builder. In the example below, `$task` is your own task directory. The destination directories must already exist.

   ```sh
   python3.12 -BS -m examples.seat_handoffs.kit install "$task/environment"
   python3.12 -BS -m examples.seat_handoffs.kit install "$task/tests"
   python3.12 -BS -m examples.seat_handoffs.kit check "$task/environment" --json
   python3.12 -BS -m examples.seat_handoffs.kit check "$task/tests" --json
   ```

   The package is `handoff_v1` in each context. Compare the two package digests before the build. A changed copy is not overwritten. Include `handoff_v1/` beside the world entry point in the **world** image. For example, a Dockerfile that copies only `world.py` also needs `COPY handoff_v1 /app/handoff_v1`. Do not mount the journal or verifier directory in a seat container.

3. At startup, import `Edge` from `handoff_v1.core` and `Journal` from `handoff_v1.producer`. Enter the journal as a context manager for the whole world lifetime:

   ```python
   edge = Edge(message="revision", sender="reporter", receiver="booker")
   with Journal(log_path, (edge,), acknowledgements=True) as journal:
       serve_world(journal)
   ```

   Your world supplies `log_path` and `serve_world`. At each accepted transition, use the **same lock** as the domain state. Record `journal.sent("revision", "reporter")` once the world accepts the report. Record `journal.returned("revision", "booker")` when the world prepares the inbox reply. Only call `journal.acknowledged("revision", "booker")` after the receiver returns its validated receipt. Record `journal.commit("booker")` at the first irreversible booking. Each call returns `True` on its first entry and `False` on the same legal repeat. A failed domain write after an append must call `journal.fail()` and stop play. The journal and business state are not a shared transaction.

4. Freeze **both** world mutations and the journal under that lock. `journal.freeze()` returns the run UUID and `events`, which includes the header. Save the frozen domain state at the same cut. In the verifier, validate the domain state and compute its grade independently. Use `handoff_v1.producer.seal(world_bytes, cut, grade)` to produce the separate `verifier/handoffs.jsonl` file. Do not let the world append the closing record itself. Use `None` if there is no world grade to attach. A closing record is not a valid native grade on its own.
5. In `task.toml`, use `artifacts = ["/logs/world/handoffs.jsonl"]` for the optional open prefix from the main service. Do not declare a competing exporter for the same destination. Use `verifier/handoffs.jsonl` for the sealed copy. Publish your own verdict under its own schema. A `decision.json` with a foreign string schema is not a Revised Dispatch verdict. A missing schema is ambiguous and is not ignored.
6. Give the analyst the task directory and **distinct** saved control journals. Run `qualify` for the edge and boundary that the planned study needs. The two controls must have the same edge set and acknowledgement capability. A closed world without a decision does not supply the contrasting decision. A task roster match is not proof of journal origin. Preserve the task and execution's native locks separately.

The [original world](../tasks/revised-dispatch/environment/world.py) and [native verifier](../tasks/revised-dispatch/tests/verify.py) show these boundaries in a working task. They are frozen examples, not dependencies of the kit. The `v1` format supports one commit per receiver and the first handoff at each named edge. Do not use it to claim that an agent read a message, followed it or changed its mind. For retrying teams, [keep one receipt per execution](README.md#keep-message-evidence-when-harbor-retries-a-slot).
