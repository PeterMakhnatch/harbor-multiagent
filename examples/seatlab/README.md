# SeatLab: test what reaches a Harbor seat

Use SeatLab when two multi-agent runs have the same reward and you need to know **whether the actors saw and did the same things**. It works today on Olam's native multi-seat fork. You can verify the included study without Docker, an account, or a model.

### 1. Read the evidence in under a minute

```sh
git clone --branch experiments/seatlab-handoff https://github.com/PeterMakhnatch/harbor-multiagent.git
cd harbor-multiagent
python3 examples/seatlab/export_study.py verify examples/seatlab/results/primary.json
python3 examples/seatlab/export_study.py verify examples/seatlab/results/heldout.json
```

Use Python 3.12 or later. See [observed results and limits](RESULTS.md), the [prospective protocol](protocol.md), [native integration controls](baselines.md), and the [held-out replication](replication.md). The verifier recomputes the score and checks the published records for consistency. It does not prove that a third party executed Docker.

### 2. Run two real participants locally

Start Docker with a local Unix socket and the Compose plugin. Install this checkout with `uv sync --locked` (tested with `uv` 0.13.9). Use a new output path for each run. No API key is needed.

```sh
out="$(mktemp -d)"
python3 examples/seatlab/run_panel.py --harbor "$PWD/.venv/bin/harbor" \
  --out "$out/revision" --policy oracle --seeds 17 --episodes 12 \
  --conditions silent,stale,fresh,reordered,pooled
python3 examples/seatlab/paired.py --panel "revision=$out/revision" --reference silent
python3 examples/seatlab/run_panel.py --harbor "$PWD/.venv/bin/harbor" \
  --out "$out/arrival" --policy arrival --seeds 17 --episodes 12 \
  --conditions stale,fresh,reordered
python3 examples/seatlab/paired.py --panel "arrival=$out/arrival" --reference fresh
```

Inventory reports availability. Dispatch knows utilities and selects a bay. The verifier checks the **submitted** choice. `fresh` and `reordered` include the same current report. The latter also includes an old report that arrives last. The 2 policies above interpret that order differently. Use `paired.py --design` for the finite-design reference, not as a substitute for an actual run. The historical primary run uses [this execution revision](https://github.com/PeterMakhnatch/harbor-multiagent/commit/dc7722833f6d5d4d0726ccbc6755ddaa42faea9b).

### 3. Compare independent runs or query Eval Lab

Repeat the same complete panel into a new directory. Run `compare_replay.py --baseline FIRST --candidate SECOND`. It compares latent inputs, each actor's view, reports, decisions and score. Exit `0` means match, `1` means measured drift, and `2` means invalid input. It rejects comparisons of a run with itself. A reconstructed view is not proof that the original client received its bytes.

Pass a completed native trial directory to `export_capsules.py` to emit role-local JSONL. Send **only `capsule.view`** to an actor. The surrounding index is analyst data. Do not send a world dossier: it includes information the actor must not see.

For an existing Eval Lab checkout, use its Python environment to run `query_evallab.py --help`. The bridge uses a new scratch directory and an in-memory census. It preserves separate trial and seat denominators. Native trial-only output may have no job row. The [native job control](baselines.md) shows a successful job/seat join. Do not infer zero tokens or cost from missing fields.

### 4. Extend it in parallel

| Area | Entry point | Contract |
| --- | --- | --- |
| Worlds and scorers | [`../tasks/dispatch-handoff`](../tasks/dispatch-handoff) | Keep private state in the world. Score actual committed actions. |
| Actor policies | `decisions.py`, `seat_agent.py` | Operate on the role view only. Keep native model failures unmeasured. |
| Observation and replay | `capsule.py`, `export_capsules.py`, `compare_replay.py` | Keep actor input separate from analyst metadata. Label reconstructed and client-acknowledged evidence. |
| Analysis and release | `paired.py`, `export_study.py`, `query_evallab.py` | Reopen native evidence. Reject duplicate trial IDs. Keep absent values unknown. |

Use unique output directories and a new study record for new arms. Raw native directories can contain private paths or provider messages. Share an allowlisted bundle rather than copying those directories. No modification to Olam or Eval Lab is required to use these tools.