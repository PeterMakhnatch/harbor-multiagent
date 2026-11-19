# Native five-person cursor negotiation

**The native coordinator and the offline simulation agree on every scheduled time and all 7 resource counters in these 24 cases.** Five independent participant processes exchange private information through a sixth world process. The world does not select a time or the next bound. Only the coordinator sees a complete response bundle. These are real Harbor trials with scripted actors; there are no model calls.

| Registered group | Distinct trials verified | Official and physically feasible choices | Complete rounds | Scalar disclosures | Canonical payload bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| Development: first case in each stratum | 4 / 4 | 4 / 4 | 75 | 375 | 2,014 |
| Follow-up: next 5 cases in each stratum | 20 / 20 | 20 / 20 | 398 | 1,990 | 10,612 |

The verifier independently checks each participant's response against its private calendar. It checks that the final coordinator action acknowledges the terminal bundle. It checks the published reference and physical feasibility separately. The 7 resource counters are rounds, seat exchanges, scalar disclosures, distinct disclosed starts, request bytes, response bytes, and total payload bytes. They exclude JSON envelopes, filesystem transport, Docker time, and inference. Summed within cases, the 2 groups disclose 292 and 1,569 distinct `(participant, start)` pairs respectively. No failed, incomplete, or null choices occurred in these groups. This does not establish results for a learned coordinator or for all 200 cases.

The [development records](results/interactive-smoke.json) and [follow-up records](results/interactive-followup.json) contain the native UUID and observed values for each case. The [registered design](cohort-protocol.md) fixes which cases count. The independent [offline cursor implementation](cursor.py) does not receive the native result.

## Recompute from the public native projections

Clone the [published source dataset](https://github.com/BUPT-GAMMA/MASBench/tree/c0301a650db95958924bb080192c09fef39389a5) at commit `c0301a650db95958924bb080192c09fef39389a5`. From this adapter directory, pass that checkout as `MASBENCH_DATASET`. This checks observations against the independent, deterministic simulation. It does not relaunch a native trial or authenticate the publisher.

```sh
python - "$MASBENCH_DATASET" <<'PY'
import json, sys
from pathlib import Path
from cases import load_case
from cursor import analyze_case
seen = set()
for group in ('smoke', 'followup'):
    rows = json.loads(Path(f'results/interactive-{group}.json').read_bytes())['cells']
    for cell in rows:
        assert cell['status'] == 'verified' and cell['native_id'] not in seen
        seen.add(cell['native_id'])
        native = cell['observed']
        offline = analyze_case(load_case(Path(sys.argv[1]), cell['case_id']))['modes']['cursor']
        assert native['choice'] == offline['start']
        assert native['reward'] == offline['reference_score']
        assert native['physically_feasible'] == offline['physically_feasible']
        assert native['resources'] == offline['resources']
    print(group, len(rows), 'matched')
assert len(seen) == 24
PY
```

For a fresh trial, install this Olam fork with Docker available. Run `python interactive.py run --dataset "$MASBENCH_DATASET" --cases 2/1 --harbor "$(command -v harbor)" --out NEW_PRIVATE_STUDY`. Run `python interactive.py collect --out NEW_PRIVATE_STUDY` to revalidate retained native evidence; it does not start participants. Never reuse the study directory to replace a measurement.
