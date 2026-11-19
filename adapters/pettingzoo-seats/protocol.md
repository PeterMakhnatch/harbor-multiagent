# Does a Harbor seat preserve the game's decisions?

## Frozen first experiment

Run 8 new stock Olam Harbor trials, using the same seed (19) in 2 real PettingZoo games. Fix each seat's policy before the run. Do not replace a failed trial. This is a transport and attribution test, not a comparison of model ability.

| Game | API | Registered cells, in order | Stop condition |
| --- | --- | --- | --- |
| Rock Paper Scissors (`rps_v2`) | Parallel | `rps-ll-a`, `rps-ll-b`, `rps-lh`, `rps-copy-beat` | 6 simultaneous moves per seat |
| Connect Four (`connect_four_v3`) | AEC turns | `connect-ll-a`, `connect-ll-b`, `connect-lh`, `connect-hl` | The underlying game ends |

`l` chooses the lowest legal action. `h` chooses the highest. `copy` repeats the previous observed opponent move. `beat` plays its successor modulo 3. The 2 RPS response policies start at 0. The two `ll` cells use fresh native trials. Do not interpret a new trial UUID as a new world seed.

## Checks registered before the native run

1. Use distinct Harbor-issued trial UUIDs. Require the world log, one real result per seat, private action acknowledgments, and one scalar for protocol completion. Missing evidence is not zero payoff.
2. Reconstruct each native game directly through the official installed PettingZoo API, without importing the adapter world or its policies. Compare the acting role set, local observations, legal actions and actual decisions at every transition. Compare each participant's accumulated return and its final termination and truncation flags. This checks the bridge against the same engine, not the engine against an independent implementation of the rules.
3. Compare the two `ll` runs per game in world order. Expect equal views and decisions at every step. For each other arm, keep the world seed fixed and compare against the first `ll` run. Stop comparison at the first changed group. Do not assign downstream actions to the original trajectory. In RPS, both decisions happen in one group. In Connect Four, one seat acts per move.
4. Report completion separately from each player's return. Neither a draw nor a loss is a failed transport. RPS stops at its fixed horizon; Connect Four can terminate at a win or a full board.

The first experiment uses only the 8 named cells. A later game, seed, policy or retry must receive a new label. Reference diagnostics before registration inspected each API on the host. None was a native trial from this matrix.
