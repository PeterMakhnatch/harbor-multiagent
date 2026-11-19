# Pre-native forecast: 64 full Hanabi decks

The [frozen game and actor source](https://github.com/PeterMakhnatch/harbor-multiagent/tree/1d364512fa600b7d2f5c5200d82cafad349d035f/adapters/hanabi-crossplay) generated all 256 forecasts. The [forecast file](https://github.com/PeterMakhnatch/harbor-seatlab/releases/download/v0.8.0/hanabi-predictions-64.json) has SHA-256 `9302b9818793a5dbd8efaf5e300684395ec13d7495d84ab4e33c93f8432f91f9`. We downloaded it and checked the digest before starting the 32 registered Harbor trials.

| Pairing | Games | Mean shared score (out of 25) | Zero scores |
| --- | ---: | ---: | ---: |
| LL | 64 | 5.5 | 48 |
| LR | 64 | 0 | 64 |
| RL | 64 | 0 | 64 |
| RR | 64 | 6.46875 | 45 |

The mean within-deck self-play advantage is **5.984375 points**. Self-play scores higher on 22 decks, ties on 42 and scores lower on 0. These are deterministic actors on this fixed deck set. Neither convention is a strong Hanabi player. This is not a model ranking, a training experiment, or 256 independent decks.

The file includes each deck digest, decision count, accepted-history digest and score. Its forecasts run the complete game from the start. They do not reuse future observations from a game with a different partner. The independent native comparison uses deck IDs 0–7. A separate stock-Docker transport diagnostic on deck 90 and endpoint checks outside 0–63 do not count toward either denominator. See the [registered protocol](protocol.md).
