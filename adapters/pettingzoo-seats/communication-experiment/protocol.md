# Registered study: does a message change coordination, or just its spelling?

Use a real cooperative particle world, not a language-model judge. The speaker alone sees the target color. The listener alone can move. The listener sees its own velocity, all 3 landmark offsets and the last received signal. The target color is absent from its view. Each seat acts simultaneously. A new message becomes visible to the listener only on the next decision. The official payoff is the negative squared distance from the listener to the target, accumulated over 20 world steps. Keep both per-role payoffs, but do not count a shared team payoff twice.

## Freeze before the first measured run

Use `mpe2==1.1.1` and `mpe2.simple_speaker_listener_v4:parallel_env` with `continuous_actions=false`, `max_cycles=20`, seeds **5, 19, 73**. Bind `p0` to `speaker_0` and `p1` to `listener_0`, with 2 trusted independent executables under the stock Olam Harbor runner. Use the same spec for all teams on a seed. No model, network call, parameter search, rerun or replacement seed. Give each executable just its own current view.

For each of the 3 seeds run 5 teams: `plain-plain`, `rotate-rotate`, `plain-rotate`, `rotate-plain`, `plain-blind`. Names are in speaker–listener order. Cell names are `s<seed>-<team>`. Publish the 15-cell denominator, including any invalid or missing native episodes. Do not select seeds based on the goal or results.

- A plain speaker sends the index of the largest target-color component. A rotated speaker sends `(index + 1) % 3`.
- A listener with a plain or rotated codebook subtracts its own offset (0 or 1) from the received channel modulo 3. A blind listener always pursues landmark 0 once a signal exists. It still sees the original input. This is a policy control, not removal of the signal from the world.
- All listeners wait (`0`) when the 3 communication components are all zero. Otherwise compute `d = relative_position - 0.6 * velocity` for the chosen landmark. If `max(abs(d)) < 0.06`, wait. Otherwise move on the axis with larger magnitude; x wins ties. Codes are left 1, right 2, down 3, up 4. Programs do not save state or inspect a role outside their current view.

## What separates communication from observation bytes

Against `plain-plain`, report first changed speaker action, listener channel, listener physical observation and listener action in **world order**, comparing both roles at each joint step. Report the independent direct-engine measurements, exact actions, terminal flags, raw returns and observed maximum numeric error (tolerance `1e-6`). A matched rotation should change speaker symbols and later listener input bytes, while preserving the listener's physical trajectory. A mismatched listener can select a different destination. Do not infer the sign or size of a score effect, or that all seeds must differ. This contrasts convention compatibility, not agent intelligence. The fixed blind destination may coincide with the true target.

The independent reference uses the official MPE2 parallel API, not the adapter or executable policy code. An independent native run is the authority for each comparison. A conditional simulation or a second parser alone is not a second Harbor trial. Scripts, public numeric evidence, and missing-attempt receipts belong on the personal fork. Do not publish host logs, process environments, or trial configs.
