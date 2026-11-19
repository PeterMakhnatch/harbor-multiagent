# MASBench Scheduling with five native Harbor participants

Each agent owns a private calendar. A coordinator must select a 20-minute interval that all five people can attend. This adapter ports the **released calendar data and exact reference answers**, not the original model conversation. It adds two deterministic baseline protocols, an independent outcome verifier, and a private-input communication study.

## Reproduce the dataset audit

Use this branch of [Peter's Olam fork](https://github.com/PeterMakhnatch/harbor-multiagent/tree/experiments/seatlab-masbench-calendars). Install its lock with `uv sync --locked` using uv ≥0.13.5. Clone the [original MIT-licensed data](https://github.com/BUPT-GAMMA/MASBench/tree/c0301a650db95958924bb080192c09fef39389a5/Scheduling) separately.

```sh
git clone --filter=blob:none --sparse https://github.com/BUPT-GAMMA/MASBench.git /tmp/masbench-data
git -C /tmp/masbench-data sparse-checkout set Scheduling
git -C /tmp/masbench-data checkout c0301a650db95958924bb080192c09fef39389a5
d=adapters/masbench-scheduling
.venv/bin/python "$d/study.py" audit --dataset /tmp/masbench-data
.venv/bin/python "$d/cursor.py" analyze --dataset /tmp/masbench-data
```

The importer checks each member's dated intervals independently and keeps the upstream reference unchanged. Across the full release, **all 200 reference sets agree with the independently derived calendars**. Their union by case includes 700 valid case/start pairs. There are 40 cases with 2 official choices, 60 with 3, 60 with 4, and 40 with 5. This is a source audit, not a model result.

## What changes when members disclose less?

The following is an **offline protocol simulation on all 200 actual scenarios**. The coordinator in the cursor protocol receives responses from five private iterators, not copies of their calendars. It repeatedly asks for the earliest local start at or after a shared lower bound. When all five answers match, that start is the earliest common one. If answers differ, the maximum reply becomes the new bound. The bound strictly advances. One exhausted iterator ends the search.

| Exchange | Solved / 200 | Mean rounds | Mean scalar disclosures | Mean payload bytes |
| --- | ---: | ---: | ---: | ---: |
| Full local availability | 200 | 1 | 768.5 | 2,993.6 |
| First 8 local starts | 15 | 1 | 40.0 | 125.2 |
| Cursor, one candidate at a time | 200 | 19.46 | 97.3 | 515.4 |

Cursor recovers the same earliest start as full disclosure in every case. Its worst case takes 59 rounds. The useful design question is not simply whether to shorten the context. It is which extra round trips buy back correctness, and whether their latency fits the task. Bytes count canonical message bodies, not network frames or model tokens. Fewer disclosed starts do not establish stronger privacy. See the [registered cursor design](cursor-protocol.md).

## Run an actual five-seat world

Start local Docker with the Compose plugin. Use a new output path. No model, provider account, or cloud service is needed.

```sh
out="$(mktemp -d)"
.venv/bin/python "$d/study.py" run --dataset /tmp/masbench-data \
  --out "$out/first-cases" --harbor "$PWD/.venv/bin/harbor" \
  --cases 2/1,3/1,4/1,5/1 --modes full,first8
.venv/bin/python "$d/study.py" collect --out "$out/first-cases"
```

`m1` through `m5` are separate Harbor participants. Each seat has one private exchange volume. Only the world container has the original reference answers. All five seats submit observations and reports. Only `m1` receives all reports and submits the decision. The verifier independently computes the shared accuracy and physical feasibility. An explicit null means that the team did not schedule a meeting. Missing evidence is not a zero.

Use `--shard i/n` with the same ordered cases and treatments to divide native runs between independent workers. Use different output directories. The `collect` command reads existing trials without starting new ones. Private output contains calendars and host paths. Publish only approved fields. [Protocol](PROTOCOL.md) · [upstream license](UPSTREAM-LICENSE.txt).

## Run the bounded native cursor treatment

On the `experiments/seatlab-cursor-budget` branch, use `interactive.py` with a new private output path:

```sh
budget_out="$(mktemp -d)/budget"
.venv/bin/python "$d/interactive.py" run --dataset /tmp/masbench-data \
  --cases 2/1,3/1,4/1,5/1 --harbor "$PWD/.venv/bin/harbor" --out "$budget_out"
.venv/bin/python "$d/interactive.py" collect --out "$budget_out"
```

This treatment keeps the cursor messages and private cases unchanged. After 8 complete rounds, `m1` submits a null choice if the replies still disagree and no seat is exhausted. A unanimous or exhausted round takes precedence. Reports separate these 3 termination reasons for verified trials. A missing action is not a null choice.

Use the original interactive branch to collect unbounded receipts. This treatment does not change the offline cursor, `full`, or `first8`.