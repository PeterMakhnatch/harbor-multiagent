# One changed rule, with all native completions still green

The [registered protocol](https://github.com/PeterMakhnatch/harbor-multiagent/blob/ee0127b2c1/adapters/pettingzoo-seats/communication-experiment/panel-change-protocol.md) predicted which blind-listener results would change before these 10 new native trials ran. The actor changes the blind target from landmark index 0 to index 1. Both the verifier and the other 4 team definitions stay unchanged. The 2-world panel comes from the independent 60-trial source.

| Native repeat on seeds 101 and 102 | Unchanged code on fresh Linux | One changed blind rule |
| --- | ---: | ---: |
| Harbor completions | 10 / 10 | 10 / 10 |
| Stable registered case-by-criterion states | 18 / 18 | 10 / 18 |
| Changed registered states | 0 | 8 |
| Identical accepted actions against the same team's source | 400 / 400 | 392 / 400 |
| Identical private views | 400 / 400 | 369 / 400 |
| Identical per-role simulator returns | 20 / 20 | 16 / 20 |

On seed 101 the blind listener now differs from the informed listener. On 102 it now matches. Four criteria (equal and different for moves and physical inputs) reverse on each seed. These 8 changed entries are overlapping criteria in 2 worlds, not 8 independent failures. On both worlds the other 4 teams reproduce their original actions and views. Shared completion and game return are different quantities.

[Independent Linux job](https://github.com/PeterMakhnatch/harbor-seatlab/actions/runs/44549885787) · [unaltered and changed raw projections](https://github.com/PeterMakhnatch/harbor-seatlab/releases/tag/panel-control-data-v1) · [changed actor](https://github.com/PeterMakhnatch/harbor-multiagent/commit/4cfeb66a1a) · [machine receipt](panel-change-results.json). These are fixed programs. This result does not establish a model effect, an arbitrary regression-detection rate, or generalization outside the selected worlds.
