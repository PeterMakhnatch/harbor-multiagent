# One partner changes the other chef's next input, not its action script

A team reward alone hides the useful debugging detail. In this task, replacing either chef with a wait policy prevents delivery. The other chef still takes **all 38 original actions** in the independently run world. Its first different input arrives at turn 2. Recording both the input and the action separates these facts.

| Native arm | Registered trials | Measured grades | Deliveries in measured trials |
| --- | ---: | ---: | ---: |
| Original stock runtime, all arms | 6 | 0 | Unknown |
| Fixed runtime, both follow the tape (TT) | 2 | 2 | 2 |
| Fixed runtime, chef0 waits (WT) | 2 | 2 | 0 |
| Fixed runtime, chef1 waits (TW) | 2 | 2 | 0 |

The [original 6 trials](seatlab-first-run.md) stopped before the actors started. None received a grade. The separate [runtime control](seatlab-runtime-control.md) used the same task capsule with 6 new trial UUIDs. The fixed runtime changed only how Harbor stages a private oracle solution for a nonroot seat. Do not pool the two cohorts. The control contains 228 native joint transitions and 456 accepted seat actions. Chef1 made both deliveries, one in each TT repeat. Both TT histories match all 38 turns for both roles.

## Where to look in a trace

In all 4 unilateral comparisons, the substituted chef changes its action on **turn 1 with an equal input**. On turn 2 the unchanged chef receives a different state. With chef0 waiting, the first changed value is chef0's position. With chef1 waiting, it is chef1's orientation. The unchanged chef still follows the same tick-indexed script. The input receipt proves what it received, not which fields it used.

A source-only wait policy predicted turn 1 as the first different action in each comparison. It stopped there and withheld 148 later frames across 4 source-role episodes. Their recorded inputs do not constitute a future under the changed policy. The delivery claims above come from fresh native worlds, not these source-only predictions. The 4 comparisons share TT controls and are not 4 independent teams.

## Reproduce the result

[Download the native projection and analysis](https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/v0.6.0/native-kitchen-six.tar.gz). The archive SHA-256 is `76a13fa8e288e07e6803ef99ae50b55671c7f5af50cb93d0723fdfdf579624a8`. It includes both original denominators, the 6 measured episode tapes, the 5 native pair manifests, the comparison reports and the upstream Overcooked-AI license. It is not a full Harbor job archive.

```sh
# From this task directory. Use a Python environment with harbor-seatlab 0.8 installed.
python analyze_seatlab.py --original "$DATA/original/index.json" \
  --control "$DATA/control/index.json" --out "$DATA/recomputed.json"
cmp "$DATA/analysis/analysis.json" "$DATA/recomputed.json"
seatlab align "$DATA/control/A-TT.jsonl" "$DATA/control/A-WT.jsonl" \
  --pairs "$DATA/control/A-TT-vs-A-WT.json" --input-paths 8
```

The [portable SeatLab study](https://github.com/PeterMakhnatch/harbor-seatlab/blob/0f47ae0785d60aeed7c675d4e5e40ea18ecb6068/docs/portable-studies.md) also verifies all 5 kitchen alignments without running code from the archive. These results test a harness with deterministic scripts. They do not measure a model's ability to cook or coordinate.
