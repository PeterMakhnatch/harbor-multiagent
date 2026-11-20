# Seat attribution experiments

Executed evidence about whether a seat trial's reward credits the seat
that did the work. Start with `RESULTS.md` (every row, with costs) and
`LEARNINGS.md` (what transfers to other seat tasks).

| Piece | What it is |
|---|---|
| `run_arms.py` + `agents.py` | Stock hello-seats arms: honest oracle pair, sleeper+impersonator (false credit), lazy support. No model calls. |
| `run_delay.py` + `delay_agents.py` | Model listener vs scripted delayed partner on the token-auth task. Set `SEAT_TASK`, `SEAT_DELAY_SEC`, `SEAT_MODEL`. |
| `tasks/liar-scout/` | Trust task: scripted scout claims a code (truth or lie), listener may verify before its one answer. Oracle 1.0, nop 0.0. |
| `tasks/liar-scout-gated/` | Same task with a world gate: `/answer` rejects any code without a prior matching `/verify`. |
| `run_liar.py` + `scout_agent.py` | Truth/lie episodes; `SEAT_MODEL`, `SEAT_TAG`, `SEAT_EP_OFFSET` select model and episode names. |
| `run_recovery.py` + `recovery_scout_agent.py` | Recovery protocol: scout corrects itself only after a listener complaint. Needs the recovery listener instruction (see RESULTS). |
| `model-job.yaml` | Both-seats model job for hello-seats (variance baseline used n=10 from a copy). |

Trial output lands under `/tmp` (`seat-exp-trials`, `liar-scout-trials`,
`seat-exp-jobs`); nothing here writes into the repository. Model rows need
`OPENROUTER_API_KEY` (e.g. `keys run -- uv run python ...`).
