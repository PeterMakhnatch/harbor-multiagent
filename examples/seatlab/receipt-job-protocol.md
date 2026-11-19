# Native identity and reward accounting inside one Olam job

## Question

Can an Eval Lab user join both roles of an Olam multi-agent trial to the correct Harbor job without counting the shared outcome twice? The [standalone study](native-model-results.md#can-stock-eval-lab-read-these-trials) found trial UUIDs but no native job UUID. That result does not establish how native jobs behave.

## Registration

Use stock Olam Harbor `6aeb609e31fd77e5f89ce3bcb018db8045520fb7` and the [receipt-board task](https://github.com/PeterMakhnatch/harbor-seatlab/tree/8f97a2902925bb25198612cb1e7df273e9bd253f/examples/receipt-board). Submit **one native job** with 2 local task variants. Use both stock oracle seats, `p1` and `p2`, in both trials. Run sequentially on local Docker with no model, no retry and no replacement. Do not start standalone trials and assign a job UUID afterward.

| Registered slot | Context | p1 expected choice | p2 expected choice | Expected native team reward |
| --- | --- | --- | --- | ---: |
| 1 | full | cobalt | coral | 1 |
| 2 | first | amber | mint | 1 |

The task-world choices establish arm membership independently of a directory name. The hypotheses are 1 job UUID, 2 distinct trial UUIDs, 4 seat records and 2 shared rewards. Native token counts for deterministic actors can be missing; do not replace missing token counts with zero.

## Verification

1. Inspect the job and trial results written by Harbor. Never derive a native UUID from a path.
2. Read the actual trial directories with stock `evallab.results.load_trial`. Open the stock trial census read-only with an empty temporary derived directory. Do not attach to a user's database or publish a projection.
3. Compare the job and trial UUIDs, seat membership and shared reward separately. Match actual received choices to the registered arms. Keep unexpected, missing and failed evidence visible.
4. If the stock census will not project a reward with no prior ingestion, report that outcome. It is not a censored zero and does not invalidate direct native team rewards.

Freeze this protocol before the Harbor job starts. Publish only the derived report and observed limitations. It is a compatibility test at 2 source revisions, not a quality or speed estimate.
