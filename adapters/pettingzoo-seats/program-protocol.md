# Registered experiment: does the same executable work offline and as a real participant?

The bridge study uses installed oracle actors. This extension uses a stock Harbor `BaseAgent` plugin to run operator-supplied programs. It does not change the 8-cell denominator. The game, seed, seat mapping, rules and horizon match the original RPS and Connect Four presets. Each cell must have a new native trial ID. Attempt each once. No model, retries, or dependencies in the SeatLab analysis package.

| Cell | Game | p0 | p1 | Independent native reference |
| --- | --- | --- | --- | --- |
| `program-mixed` | RPS | JavaScript: copy last move | Stock oracle: beat last move | `rps-copy-beat` |
| `program-both` | RPS | Same JavaScript program | Python: beat last move | `rps-copy-beat` |
| `program-second` | Connect Four | Stock oracle: first legal | JavaScript: last legal | `connect-lh` |
| `program-invalid` | RPS | Python program: emit a JSON boolean instead of an integer | Stock oracle: first legal | No accepted game expected |

A program receives only the current actor view as JSON on stdin. It returns one JSON object with an integer `action`. The framework, not the program, joins the action to the step and native receipt. The JavaScript and Python programs implement the rules independently of the installed oracle. Do not silently substitute a reference policy or rerun a failed process. A trusted host executable is not a sandbox.

For each valid cell, compare the private actor views, world steps, actions, per-player returns and end flags with the named native reference. In RPS, count both simultaneous actions once per world step. In Connect Four, the external program should run only for the second player’s turns. Run the same external executables over the actual recorded role views after the native attempts. Compare their outputs to the recorded actions without granting them future observations or labels. Record each participant type and real program call count. Do not treat `null` native tokens as measured zeros. For the invalid cell, preserve the native exception or failed verifier and stop; do not project an absent game as a zero payoff. This tests interoperability, not policy quality.
