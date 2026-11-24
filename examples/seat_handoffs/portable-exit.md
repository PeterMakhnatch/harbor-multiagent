# Replay a real team cohort without the original machine

The [sample](samples/portable-exit/manifest.json) contains observations from 8 real Flotilla scheduling jobs. Use it to ask: **what grade could the shared calendar have held when one seat returned while its peer still ran?** This is not a recreation of either model. No seat was stopped.

## Run it

From the fork checkout, use Python 3.12 or later. The sample needs no installation, Docker, network, API key, or raw trial directory.

```bash
pack=examples/seat_handoffs/samples/portable-exit
python3 -BS -m examples.seat_handoffs.pack_replay "$pack/manifest.json"
python3 -BS -m examples.seat_handoffs.pack_replay "$pack/manifest.json" --clock-ms 1000
python3 -BS -m examples.seat_handoffs.pack_replay "$pack/manifest.json" --grid
```

The default uses both-seat completions and makes no host/world clock alignment assumption. A numeric allowance assumes a fresh stock world and a bound on elapsed-clock disagreement. The archive does not certify the allowance. `--grid` runs all registered allowances with and without verified timeout grades. Use `--json` for individual admissible prefixes and grades. A range is a set of possibilities, not a confidence interval.

The manifest fixes the denominator. Job shards can travel separately. With no explicit shard paths, the reader uses adjacent `job-*.json` files. To model an absent job without editing the sample:

```bash
python3 -BS -m examples.seat_handoffs.pack_replay "$pack/manifest.json" "$pack"/job-00[1245678].json --clock-ms 1000
python3 -BS -m examples.seat_handoffs.pack_replay "$pack/manifest.json" --job 3 --clock-ms 1000
```

The first command omits job 3's evidence. It keeps all 192 registered slots. Its loss bounds use only the surviving witnesses. The second command measures job 3 alone, **using witnesses from every supplied eligible job**. These are intentionally different operations. Conflicting grades for the same task quarantine that task. Missing or damaged evidence never becomes a grade of zero. A second copy of the same shard cannot add teams.

## What changed when we lost evidence?

- The 192 slots contain 117 completed teams, 54 additional teams with verified grades after a timeout, 9 incomplete records, and 12 absent attempts.
- Without clock alignment, the completed teams could lose **0–54** native successes if the standing board were frozen. At each tested allowance from 0 through 2,000 ms, the minimum loss is **2**. At 5,000, 10,000, 30,000 and 60,000 ms, it is **1**. None of the 11 settings identifies whether the net change is positive or negative.
- At 1,000 ms, allowing an independent answer key for every completed team gives an upper net change of **+41**. Requiring one shared key per actual task reduces it to **+34**. Summing per-team intervals does not give a sharp team-cohort result.
- We omitted each shard once at 1,000 ms. Losing one shard widened a surviving team's grade interval in **6 of 8** omissions. Losing job 3 removed 24 completed teams. It also made the grades of **4 remaining teams** less certain. The full manifest still registered 192 slots. At least 1 conditional loss remained after every single omission.

We compared 2 independently blinded exports against the original native reader at 11 settings in 2 populations. Each export matched **3,168 per-team projections**. On the public export, the audit matched every grouped case and every reported cohort. The [frozen question](samples/portable-exit-question.json) and [results](samples/portable-exit-outcomes.json) give the populations, exact bounds and transfer checks. This is the same model cohort, not replication on new teams.

## Release or audit your own pack

Pass distinct native scheduling job directories in a fixed order. Each needs its original `lock.json` and its available first-attempt directories. The exporter refuses to overwrite an existing directory. A published manifest binds exact shard bytes; it does not authenticate the owner.

```bash
private=$(mktemp -d)
python3 -BS -m examples.seat_handoffs.pack_owner new-release /private/job-a /private/job-b
python3 -BS -m examples.seat_handoffs schedule-jobs /private/job-a /private/job-b --seat-exit --exit-clock-ms 1000 --json > "$private/native-stock.json"
python3 -BS -m examples.seat_handoffs.pack_replay new-release/manifest.json --stock "$private/native-stock.json"
```

Keep the raw stock report private. An audit compares cases grouped by task, up to relabeling. It does not assume that public row positions match original trials. It also compares all reported cohort bounds and coverage counts. For a new release, run this audit on two-seat jobs. Mixed rosters sharing one native task key are not supported.

The release replaces each task digest and permutes its 35 possible choices. It preserves within-task equality, write order and counts, test condition, native binary reward, and coarse return intervals. It contains no calendar answer strings, messages, native IDs, exact times, model names, file paths, or private key mapping. Distinctive groups can still be recognizable. The released data cannot validate a host clock, recover the original task, change the return rule, or say what a live model would do after an intervention.
