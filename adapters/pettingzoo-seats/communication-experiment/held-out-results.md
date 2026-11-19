# When a blind listener looks informed

A listener that ignored every incoming symbol made **all the same moves** as an informed listener on 5 of 12 new worlds. Those 5 worlds all had the blind listener's fixed target. On the remaining 7 worlds at least one move differed. Every one of the 60 native teams earned completion reward 1. Neither an equal score nor identical movement in one world established information use.

We registered consecutive seeds 101–112, 5 fixed policy pairs per seed, and 20 simultaneous decisions per role. No model, retry, or replacement cell was used. The native baseline speaker's first accepted symbol defines the 2 groups. The blind actor receives the channel like every other listener. Only its policy ignores it.

| Measured control | Result |
| --- | --- |
| Blind versus informed; native target was 0 | All 20 moves equal on 5/5 worlds: 101, 105, 107, 108 and 112. |
| Blind versus informed; native target was not 0 | All 20 moves equal on 0/7 worlds. |
| Both partners rotate all symbol meanings | The first symbol changes on 12/12 worlds. All 240 paired listener moves and all 1,920 paired physical observation components remain equal. |
| Only one partner rotates its convention | Both crossed teams earn lower listener returns on 24/24 paired comparisons. These share 12 baseline trials. |

## Predictions and verification

The [protocol](held-out-protocol.md) predates simulation and native collection. The [published forecast](https://github.com/PeterMakhnatch/harbor-multiagent/blob/11905583a7a6a759f3469f6825f78c9f0c588bac/adapters/pettingzoo-seats/communication-experiment/held-out-forecast.json) predates the first native attempt. Its raw SHA-256 is `87caff7d0ff2321a6a227a1b8a5ef9c287ad6895d1bce5b0561ba18164e6c76a`. It predicts each target, each team's return, and the first changed move, first changed physical step, and number of different moves for all 48 registered contrasts. These are separate predictions, not 48 additional native trials.

All 60 native UUIDs are distinct. None is missing. The prospective forecast agrees on all 12 targets, 60 team return pairs and 144 contrast fields. A separately implemented pair of actor rules drives the official MPE2 engine. It matches all 1,200 world steps, 2,400 decisions, 16,800 observation components and 120 recorded per-role returns. Both roles share a consequence. Count 60 games, not 120 independent scores. Maximum observed numeric discrepancy is 0; the stated return tolerance was 1e-6. The [public result](../results/communication-heldout.json) retains the UUIDs and all denominators.

## Use the native data

The [data-only archive](https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/v0.13.0/native-speaker-listener-holdout.tar.gz) contains episode tapes, authoritative orders and terminal outcomes for all 60 trials. It includes the frozen forecast and its comparison with native records. The archive digest is `5212d12fa6b1a57177abd6203e6a5d7fde66f4a923edc8902bc834811ce1060d`. The manifest separately pins the 120 episode and order files used to recompute the 96 role measurements. Stock Eval Lab joins those relations to 60 native trial IDs and 120 participant executions, not 96 trials or 120 rewards.

```sh
seatlab study verify study.json --expect-sha256 34b0bfde9b2c4e7a71e2906c9c17ba8d65239defe24cd9bd01575ff888bb0d7d --html holdout.html
seatlab study panel study.json --expect-sha256 34b0bfde9b2c4e7a71e2906c9c17ba8d65239defe24cd9bd01575ff888bb0d7d --design panel-design.json
```

The study and design produce a regression panel for **9 declared behaviors**. Among these 12 worlds, 35 distinct seed pairs cover all 9. No single seed covers both equality and difference for the blind listener. The lexicographically first minimum is seeds 101 and 102. We checked all 12 singletons and all 66 pairs. For these same fixed observations, a 5-team repeat needs 10 trials instead of 60. This does not estimate performance, statistical power, or coverage of other behaviors. The [registered independent-platform repeat](panel-replication-protocol.md) tests whether the selected observations can be collected again.
