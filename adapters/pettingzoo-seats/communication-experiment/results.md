# Different signals, identical listener movement

We ran 15 separate two-seat Harbor trials of MPE2's cooperative speaker–listener world. Only the speaker sees the target color. Only the listener can move. The world steps both roles together. We fixed 3 seeds, 5 policy pairs, and 20 steps before collecting results. No model, retries or replacement seeds were used.

**Rotating both codebooks changed the speaker's symbol at step 1 on every seed. It did not change a single listener action, physical observation, or payoff.** On step 2, all 3 listeners saw a different channel. The other 8 numbers in their observations still matched. Equal physical observations here means exact agreement on 480 compared components: 8 per turn × 20 turns × 3 seeds. This is not a statement about hidden game state.

| Seed | First changed listener move for either mismatched codebook | First changed physical input | Plain/ plain return | Rotated/ rotated return | Plain/ rotated return | Rotated/ plain return | Plain/ blind return |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 5 | 2 | 3 | −28.85 | −28.85 | −89.13 | −46.60 | −46.60 |
| 19 | 4 | 5 | −5.03 | −5.03 | −10.89 | −13.08 | −10.89 |
| 73 | 3 | 4 | −1.89 | −1.89 | −20.25 | −20.09 | −20.09 |

Returns in the table are the listener's game return, rounded to 2 decimals. The task also records a speaker return. It is the same shared consequence, not a second independent score. All 15 trials have Harbor completion reward 1. A completion-only comparison therefore misses every game-level difference above. A blind listener still receives the signal; its fixed policy ignores the symbol. Its return coincides with one of the crossed conventions here because both pursue the same landmark on that seed.

## How much evidence is independent?

All 15 registered cells produced a native UUID and a complete world recording. A second implementation used the official MPE2 API and independently coded both actor rules. Against the native recordings, it matched **300 world steps, 600 actions, 4,200 numeric observation components and 30 per-role returns**. Maximum absolute observation and return differences were both 0. These are comparisons against 15 native trials, not 30 trials. The native recording does not expose a reward per step. Only the independent simulator has that field.

A whole-team prefix correctly stops when one speaker changes a symbol. It cannot answer whether the later listener motion still matches. The separate physical comparison above uses 2 observed native trajectories on the same seed. It does not substitute a simulated continuation or attribute later movements to an unobserved agent. Three fixed seeds are not estimates of general population performance or evidence of language understanding.

## Reproduce offline

Use the [SeatLab release](https://github.com/PeterMakhnatch/harbor-seatlab/releases/tag/v0.11.0). The data contains 15 native world projections, separate role-local episodes, authoritative world order, both outcomes and the independent program's report. It contains no executable code.

```sh
work=$(mktemp -d)
base=https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/v0.11.0
curl -fsSL "$base/native-speaker-listener.tar.gz" -o "$work/data.tgz"
(cd "$work" && echo '92e2c01fee3317bd14b5315a70b4dd1d7914e31cd7e3ee0da5e752ab4022f579  data.tgz' | shasum -a 256 -c -)
tar -xzf "$work/data.tgz" -C "$work"
seatlab study verify "$work/study.json" --expect-sha256 def6e88a9717b69467ccb29a1fcaaf50769fa357a8168416e13379eafa1f9482
```

The pinned study recomputes 12 native team comparisons over 30 episode and order files. The separate tarball digest covers the detailed physical analysis. For new native collection, use the fixed [registration](protocol.md), `study.py prepare`, then `study.py run` once per named cell. Run `python reference.py ROOT --out NEW.json` with `mpe2==1.1.1` and `pettingzoo==1.26.1` to check the independent engine against your own recordings. See [all native IDs and unrounded returns](../results/communication.json).
