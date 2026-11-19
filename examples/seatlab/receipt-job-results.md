# Stock Eval Lab reads a genuine two-seat Harbor job

Both stock readers identified **1 job, 2 trials, 4 participant executions, and 2 shared rewards**. The job ran both registered context variants. Each variant received a team reward of 1. No participant reported token usage. The token total is unknown, not zero.

| Native trial | `p1` choice | `p2` choice | Shared reward |
| --- | --- | --- | ---: |
| `af7504a2-005f-4340-8caa-553dddd33b5a` (full context) | cobalt | coral | 1 |
| `8fb7068e-c8a2-461c-bccf-ed3594733108` (first option only) | amber | mint | 1 |

The job UUID is `2bd2aeca-4cee-46aa-ba0e-37fce0bcc309`. The rows above use IDs from native result files, not directory names. Actual world receipts identify the two variants. Stock `evallab.results.load_trial` read the two native results. The stock `connect_trials` census matched each trial by both path and job/trial UUID. The two readers agreed on both shared rewards. The derived directory stayed empty.

This closes a specific gap from the [standalone model trials](native-model-results.md#can-stock-eval-lab-read-these-trials). Standalone trials have no native job UUID. A real job supplies that UUID, so the same census can project its rewards. Never multiply a shared outcome by the number of seats.

## Reproduce on your own machine

Use stock [Olam Harbor `6aeb609e`](https://github.com/olamlabs/harbor-multiagent/tree/6aeb609e31fd77e5f89ce3bcb018db8045520fb7) and [Eval Lab `b9dd1924`](https://github.com/PeterMakhnatch/eval-lab/tree/b9dd192458c88f1fa6b898be292a7eb94fe15a96) in separate Python environments. The [receipt-board example](https://github.com/PeterMakhnatch/harbor-seatlab/tree/8f97a2902925bb25198612cb1e7df273e9bd253f/examples/receipt-board) prepares the `full` and `first` task directories. Run from the root of this fork:

```sh
out=$(mktemp -d)
python3 examples/seatlab/receipt_job.py --prepared "$PREPARED" --out "$out/study"
"$OLAM_HARBOR" run -c "$out/study/job-config.json"
job=$(jq -r .job_name "$out/study/job-plan.json")
"$EVAL_LAB/.venv/bin/python" examples/seatlab/receipt_job_census.py \
  --job "$out/study/jobs/$job" --prepared "$out/study/job-plan.json" \
  --evallab-root "$EVAL_LAB" --out "$out/report.json"
```

Use a fresh directory for each job. The job config disables retries and uses no model. The bridge reads only that job; it does not ingest into a shared database. A new job has new native UUIDs. The [published report](results/receipt-job-census.json) has SHA-256 `633bfd3801eb8b64bcb6de52edc53ec188fe5bc7f826c54f89994ad899d67367`. This is a compatibility check at the two named revisions, not a claim about every Harbor task. See the [pre-run registration](receipt-job-protocol.md).
