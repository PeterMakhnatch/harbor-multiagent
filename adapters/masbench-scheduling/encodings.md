# The one-round control changes the engineering decision

The cursor beats full lists, but those lists are a weak baseline. **When the coordinator needs the entire availability set, send the bitmap. When it needs one meeting time with fewer disclosed ticks, test the cursor.** The bitmap is lossless and requires one exchange. The cursor recovers the same earliest time without materializing the 5 complete calendars at the coordinator.

These are controlled protocol simulations, registered [before the encoding run](cohort-protocol.md). All 4 exact protocols choose the same earliest time and match the source reference in all 200 cases. All 3 one-round encodings decode to every original reported start for all 1,000 seat/case pairs.

| Protocol | Rounds per case, mean | Canonical bytes per case, mean | Maximum bytes | Cases with the smallest payload |
| --- | ---: | ---: | ---: | ---: |
| Full sorted integers | 1 | 2,993.57 | 3,200 | 0 |
| Inclusive consecutive ranges | 1 | 2,177.36 | 2,424 | 0 |
| Fixed 720-bit bitmap, base64 in JSON | 1 | 610.00 | 610 | 77 |
| Interactive cursor | 19.46 | 515.37 | 1,646 | 123 |

No cases tie. Cursor saves just 18,927 bytes over the bitmap across all 200 cases. It requires 3,692 additional sequential rounds. At a hypothetical rate of 10,000 bytes/second, an extra round would need to cost less than **0.513 ms** to offset the saved bytes in aggregate. That calculation assumes serialized transfer and equal overhead. It is not a network measurement. Five independent seat replies can run in parallel within each round.

The protocols answer different disclosure questions. The cursor does not transmit most local feasible starts. A recipient can decode the bitmap into all of them. Fewer explicitly disclosed ticks do not prove stronger privacy. The JSON measure excludes framing, digests, polling, network latency and model tokens. The separate [native protocol](cohort-protocol.md) tests real multi-round participation.

```sh
python3 adapters/masbench-scheduling/encoding.py analyze \
  --dataset /tmp/masbench-data --jsonl /tmp/new-encoding-evidence.jsonl
```

[Machine results, including all 4 strata](results/encodings.json) · [upstream data license](UPSTREAM-LICENSE.txt).