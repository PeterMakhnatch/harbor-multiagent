# A partner swap can change the game without changing the score

The 32 registered native Harbor games matched all 32 forecasts, including the ordered history of accepted views and actions. Most games scored zero. On the 8 native decks, all 32 single-partner contrasts diverged between decisions 1 and 14. A final score alone would miss most of these changes.

| Native pairing | Games | Decisions | Mean points / 25 | Zero scores |
| --- | ---: | ---: | ---: | ---: |
| LL | 8 | 228 | 2.75 | 7 |
| LR | 8 | 130 | 0 | 8 |
| RL | 8 | 188 | 0 | 8 |
| RR | 8 | 275 | 2.75 | 7 |

Both same-convention teams scored 22 on deck 4. Both mixed teams scored 0 on that deck. The other 7 decks tied at 0 across all 4 teams. The within-deck difference between same-convention and mixed teams averaged 2.75 points. The [64-deck forecast](forecast.md) reports a larger mean difference of 5.984375 points. The 8 native decks are a subset of the 64, not an independent estimate on 72 decks.

## What the system verified

- Each registered game had a different Harbor-issued UUID. The 64 native seat processes produced 821 accepted actions across 32 games. Each verifier replayed its game from the registered physical deck. All deck hashes, accepted histories, decision counts, final raw scores and normalized verifier rewards matched the [forecast published before the native run](forecast.md). No registered game was missing or invalid. The engine and fixed policy implementations are shared. This is a cross-runtime transport check, not independent proof of the rules.
- A separate [hosted Linux job](https://github.com/PeterMakhnatch/harbor-seatlab/actions/runs/44528208635) repeated deck 0 with 4 new native UUIDs. All 139 decisions matched the original per-seat frame sequences, not merely the 4 scores of zero. This is a portability control, not 4 more experimental decks.
- The [portable world-order study](https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/v0.9.0/native-world-order.tar.gz) contains the 32 Hanabi partner comparisons and 5 separate simultaneous kitchen comparisons. `seatlab study verify study.json --expect-sha256 b59355f4770b6d834d328816ea90bc7b99dead6ef90bb8d83879965c8d177b97` recomputes all 37 checks offline. Each Hanabi comparison changes exactly one partner on the same deck and withholds both native suffixes at the first difference. The 32 contrasts reuse 32 trials; they are not independent pairs.

The [native census](results/native-32.json) includes every registered cell and its prediction. The [Linux census](results/linux-deck-zero.json) preserves the distinct repeat UUIDs. The [protocol](protocol.md) and [one-deck quickstart](README.md) explain how to repeat the experiment with no API key. These results apply to the fixed actors and decks. They do not establish an effect for trained agents or for people.
