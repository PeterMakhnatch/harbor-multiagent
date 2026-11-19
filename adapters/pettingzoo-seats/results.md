# Same game, different histories: native PettingZoo seats

**All 4 Connect Four teams earned the same final payoff. Two won in 7 moves; two needed 19.** The second player's first changed action was visible at move 2 in the low/high comparison. A payoff-only comparison misses it. SeatLab reads the moves in the order the world accepted them, not the order containers wrote their logs.

## What actually ran

Stock Olam Harbor ran 17 registered native attempts across the 4 sets below. There are 16 completed worlds, 120 world steps and 186 accepted participant decisions. One intentionally invalid program returned a JSON boolean instead of an integer. It produced no completed game and no payoff. The 17 attempts are not 17 independent game seeds. The primary seed is 19. The 4 branch simulations below are not native attempts.

| Registered set | Native attempts | Completed worlds | Accepted decisions | Independent check |
| --- | ---: | ---: | ---: | --- |
| Classic PettingZoo games | 8 | 8 | 100 | Direct official API; exact views, legal moves, actions, returns and end flags |
| MPE2 Simple Spread | 2 | 2 | 24 | Direct official API; observed maximum absolute observation and return differences both 0 |
| External participant programs | 4 | 3 | 31 | 21 valid external decisions agree with fresh executions on the recorded private views |
| Fresh Ubuntu Docker repeat | 3 | 3 | 31 | 31/31 view digests and 31/31 actions match the original native trials |

The primary RPS cells pay p0 `0, 0, +6, -3`; p1 receives the opposite return. Both agents act in each of 6 joint steps. Connect Four uses one decision per active turn, not one per seat. All 4 games pay p0 `+1`, p1 `-1`. See [the 8 primary records](results/primary.json) and their [registration](protocol.md).

The three-role Simple Spread games each stop at a 4-step time limit. Changing only p2 changes its action at joint step 1. The other two seats still have the same inputs and actions at that step. The [particle evidence](results/particle.json) retains each actual payoff; the three are not necessarily equal. Game payoffs and the Harbor completion scalar answer different questions.

## Run a policy you own

JavaScript and Python ran as genuine native participants, not as replacements for a stored result. The JavaScript second player in Connect Four acted 3 times in a 7-move game. In the 2 RPS cells the external seats acted 6 and 12 times. The 3 valid games reproduce all decisions of the independent native oracle teams. The invalid seat makes one further call. The native command still exits 0, but its inner record has no valid reward. Do not infer success from the shell status. See [the 4 cells and call counts](results/program.json). These are trusted executables, not a sandbox.

## Branch the simulator without reading the target

Each branch verifies the complete first native step and the next actual actor views. It then reconstructs the future using the registered policy and the seeded game. It compares that simulation with a separately collected native target:

| Branch from step 2 | Simulated suffix steps | Result against independent target |
| --- | ---: | --- |
| Responsive RPS | 5 | Every input, action, payoff and end flag agrees |
| Change the Connect Four second player | 6 | Reaches the native 7-move finish; source took 19 moves |
| Repeat original Connect Four policies | 18 | Entire 19-move native repeat agrees |
| Wrong RPS policy | 5 | First mismatch at p1's action in step 2 on equal input; neither payoff agrees |

The [branch evidence](results/branches.json) records the number of compared steps separately from the simulated horizon. The wrong-policy comparison stops at its first action mismatch. End flags still match and are not proof of correct play. This procedure needs an explicit deterministic simulator. It cannot forecast a human, an opaque online game, or a hosted model's new decisions.

## Reproduce the evidence without Docker

Download the [offline HTML report](https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/v0.10.0/pettingzoo.html) or verify the data yourself. Install the [SeatLab 0.10.0 wheel](https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/v0.10.0/harbor_seatlab-0.10.0-py3-none-any.whl) with Python 3.11 or later. Then run:

```sh
work=$(mktemp -d)
curl -fsSL https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/v0.10.0/native-pettingzoo.tar.gz -o "$work/native.tar.gz"
(cd "$work" && echo 'f15b5efb206a04d205c3ac6cda264d8e1847c34cee1ef20dc0c4213e4cc9779f  native.tar.gz' | shasum -a 256 -c -)
tar -xzf "$work/native.tar.gz" -C "$work"
seatlab study verify "$work/study.json" \
  --expect-sha256 5100104afd2a2bd8194ee393e9cc68628046ccd750e7adfa04f7f843cc11afcf \
  --html "$work/report.html"
```

The manifest binds 32 episode and order files. The verifier recomputes 13 pair comparisons: 8 complete matches and 5 changed steps. The whole-archive digest also covers the separate branch, program, and direct-engine reports. The [Ubuntu run](https://github.com/PeterMakhnatch/harbor-seatlab/actions/runs/44535915005) uses 3 new Harbor trial UUIDs. Its [cross-platform measurements](results/linux.json) include the actual maximum float difference. Both references use the same third-party game rules, not a second implementation of those rules.

To generate new trials, use the [four contributor lanes](README.md). To review the minimal task-author feature, see [personal-fork PR #22](https://github.com/PeterMakhnatch/harbor-multiagent/pull/22). The candidate changes no runner code and contains no experiment analysis.
