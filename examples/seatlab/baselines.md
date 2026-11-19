# Native controls and portability findings

These are observed runs on the Olam fork at `6aeb609e31fd77e5f89ce3bcb018db8045520fb7`. They use local ARM64 Docker Engine 29.8.0, Compose 5.5.1, and the repository lock. They are outside the primary comparison. No model call participates.

| Retained native trial UUID | What changed | Shared reward | Evidence |
| --- | --- | ---: | --- |
| `7bc725a5-6b66-4004-b6c9-50e0cfd9eb87` | Unmodified `hello-seats`, two stock oracles, checkout directory and solver mode `0700` | 0/1 | Both `oracle.txt` records contain `/solution/solve.sh: Permission denied`. Each seat records exit code 126. The verifier completes and the world has no notes. |
| `278412a3-38d3-431f-9109-39facef37d15` | Same task bytes and participants; a private output directory holds a copy with standard Git archive permissions | 1/1 | The world records both notes. Both the solution directory and solver have mode `0755` in the staged copy. The original checkout is unchanged. |
| `447847dd-faf2-4dbf-b9af-6560a973c4a8` | Dispatch integration job, seed 17, `fresh`, two stock oracles | 12/12 | Native job `a1b0dadb-db13-4e2e-840f-1d1b0ea6f753` records both seats, one shared reward of 1.0, and the world dossier. |

## Why the native join matters

We opened the dispatch job with unmodified Eval Lab at `17a70ff13efa2037a8b87d2b396a1cdb33a195ba`. `evallab.results.load_trial()` retained both native seat records. `evallab.storage.trials.connect_trials()` returned the job UUID, the trial UUID, and the shared reward. We joined those UUIDs against the two seat rows using DuckDB. The grouped result was **1 trial, 2 seats, 0 known input-token values**, not 2 trials or 0 input tokens. We used an explicit scratch root and an in-memory connection. We did not import Harbor in that interpreter or ingest the trial into the shared catalog.

## Reserved identity

Olam's `Trial.create(config, trial_id=...)` accepts an externally reserved trial UUID. At the base revision a seat trial returned a fresh UUID instead. The [isolated repair](https://github.com/PeterMakhnatch/harbor-multiagent/commit/0e2b6a1289681996a691bbecec4448eaada083e8) passes the reserved value through the seat constructor. The new regression fails against the base. All 25 tests in the repaired seat lifecycle module pass on Python 3.13.17. On Python 3.12.11 an existing, separate cancellation test fails on both revisions. A successful trial with the wrong UUID can break evidence joins even when its reward is correct.

A separate Docker run on the repaired branch requested `e71aee8b-5055-5c70-bfc2-033e0133ad48`. The returned object and the retained `result.json` both contain that UUID. The unmodified `hello-seats` verifier returned `1.0`.

Do not fix a control by changing its reward, running an agent as root, or changing the user's checkout permissions. Keep failed trials and control corrections as distinct records.
