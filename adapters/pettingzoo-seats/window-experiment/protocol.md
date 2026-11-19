# Registered intervention: what does 1 previous private view buy?

A game can give a role precisely the same current observation at 2 decision points. A harness can provide additional context, but a native world receipt alone does not show which prompt reached the policy. We test the distinction with a real repeated PettingZoo `rps_v2` game, not a model judge. Use the stock Olam runner, 2 independent, deterministic native program seats and a separate world. This adds 6 trials; it does not change earlier denominators.

Freeze `seed=19`, `kwargs.max_cycles=12`, `max_world_steps=128`, and the two role bindings from `../presets/rps.json`. The world owns both payoffs, moves and end flags. Participants cannot see the seed, the world step, a clock, another role's history, or the target results **in the policy input**. Executables are trusted operator code, not an OS sandbox. Each executable starts fresh for each decision. The harness owns the history.

| Cell | p0 predictor: prior records | p1 cycle: first nonzero move | p1 prior records |
| --- | ---: | ---: | ---: |
| `ascending-w0` | 0 | 1 | 2 |
| `ascending-w1` | 1 | 1 | 2 |
| `ascending-w4` | 4 | 1 | 2 |
| `mirrored-w0` | 0 | 2 | 2 |
| `mirrored-w1` | 1 | 2 | 2 |
| `mirrored-w4` | 4 | 2 | 2 |

In its own history the cycle actor uses **only its past actions**. Its sequence is `0,1,0,2` repeated or `0,2,0,1` repeated. After a nonzero move it plays zero. After zero it alternates the nonzero move; on the first such turn it uses its assigned first nonzero move. The predictor program and all its parameters are identical across all 6 cells. It observes (in its current view) the opponent's previous move. If it sees the start sentinel, it predicts zero. If it sees a nonzero move, it predicts zero. After zero, it predicts 2 if its last prior observation was 1; otherwise it predicts 1. It plays one above its prediction modulo 3. Neither actor uses hidden state outside the supplied context.

The canonical policy input has exactly `schema=seatlab.actor-window/v1`, `current` (the raw private view), and `history` (oldest to newest, up to the declared depth). A history item contains its actual prior raw `view` and its own accepted `action`. No previous policy envelope is nested. Count canonical ASCII JSON bytes, excluding the single framing newline. These are bytes, not model tokens or dollars. A trusted wrapper records a receipt for the context it sends to the executable and the returned action. Join those receipts to the authority's independent native identity, seat observations and accepted actions. Reject missing, extra, reconstructed, or inconsistent evidence, not just an incorrect final payoff.

For each native cell report separate steps, actor decisions, role returns, complete context receipts and policy input bytes. Compare the predictor's W0, W1 and W4 histories within each opponent. Expect W1 to change some actions after the same physical view and improve its observed payoff relative to W0. Expect W4 to use more bytes without a further action change, because this fixed predictor reads at most 1 prior observation. Do not assume the actual sizes or payoffs. The independent reference computes all steps with the public game and the written policy rules. The 2 specified opponent behaviors are not a population sample. No statistical or language-model performance claim. Run each named native cell once, without retry, and keep missing cells in its registered denominator.
