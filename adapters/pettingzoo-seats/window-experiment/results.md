# One past observation helped. Four cost more and changed nothing else.

We measured 6 independent stock Harbor trials of 12-round PettingZoo rock–paper–scissors. The same predictor faced 2 fixed opponents. Only its private history depth changed. Every native trial finished. Both seats' actual input receipts verified against the authority's views and accepted moves: **144 of 144 decisions** across 72 simultaneous world steps. A separate implementation using PettingZoo reproduced every action, observation digest, final flag and payoff.

| Predictor's prior turns | Predictor bytes per 12-turn trial | Payoff against ascending cycle | Payoff against mirrored cycle |
| --- | ---: | ---: | ---: |
| 0 | 1,212 | +9 | +9 |
| 1 | 1,883 | +12 | +11 |
| 4 | 3,557 | +12 | +11 |

One prior turn changed the predictor's first action on step 4 against the ascending cycle and step 6 against the mirrored cycle. With 4 prior turns it consumed **1,674 extra bytes** per trial compared to depth 1. Its actual accepted moves did not change on any of the 24 corresponding decisions. The other participant received 2,503 input bytes in each trial. Bytes exclude line framing. They are not model tokens, prices or inference times.

The two role policies are fixed programs, not learning agents. The predictor reads at most 1 prior observation by design. This study tests the harness and a known context tradeoff. It does not show that extra history cannot help a different policy. A world observation alone cannot establish which policy envelope an executable received.

## What breaks if history is discarded?

In `ascending-w1`, the full policy envelopes distinguish all 24 recorded actions across both roles. Dropping everything except the current observation forces at least 6 disagreements for any deterministic, memoryless mapping of role and current view. The fit bound describes these recorded turns, not held-out prediction or game payoff. A wrong context digest and a missing role receipt each make `verify-windows` fail without publishing policy episodes.

## Reproduce offline

Install the [SeatLab v0.11.0 wheel](https://github.com/PeterMakhnatch/harbor-seatlab/releases/tag/v0.11.0). The archive includes the 6 measured episode pairs, native order, actual wrapper receipts, independent reference outputs and native world projections. It contains no executable code.

```sh
work=$(mktemp -d)
base=https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/v0.11.0
curl -fsSL "$base/native-policy-windows.tar.gz" -o "$work/data.tgz"
(cd "$work" && echo '6eac7f457bce590a7d6cc4b553522acaadf82c462d7c525ddbf0e34f23b3c984  data.tgz' | shasum -a 256 -c -)
tar -xzf "$work/data.tgz" -C "$work"
seatlab study verify "$work/study.json" --expect-sha256 37cd9b1d87581675a52244dbf691553f6b747c7cca5ca56b2d299d9af6b974d2
seatlab verify-windows "$work/cells/ascending-w1/episodes.jsonl" "$work/receipts/ascending-w1.jsonl" --out "$work/verified-policy.jsonl"
seatlab context-audit "$work/verified-policy.jsonl" --field /current
```

The study manifest verifies 4 team comparisons over 12 episode and order files. `verify-windows` separately verifies the selected trial's actor-level receipts. The archive digest also covers the other data. To rerun the independent game check, use `python reference.py --cycle 1 --depth 1 --world "$work/worlds/ascending-w1.json"` with PettingZoo installed. To collect fresh native trials, use `study.py prepare ROOT --python ABS_PYTHON`, then `study.py run ROOT CELL --harbor ABS_HARBOR --seatlab-src ABS_SEATLAB_CHECKOUT`. Each registered cell is claimed once.

[Pre-run registration](protocol.md) · [Native IDs and exact measurements](../results/policy-windows.json) · [SeatLab policy window contract](https://github.com/PeterMakhnatch/harbor-seatlab/blob/main/docs/policy-windows.md)
