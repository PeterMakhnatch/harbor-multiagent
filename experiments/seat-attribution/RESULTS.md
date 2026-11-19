# Seat attribution experiments

Question: on a seat task, does reward 1 prove the agent under test did the
work? Method: deterministic scripted arms on `examples/tasks/hello-seats`
(local Docker, no model calls except the last row). Fork commit `6aeb609e`.
Run date: 2026-11-19.

## Arms

| Arm | p1 (under test) | p2 (support) | Task | Reward | Reading |
|---|---|---|---|---:|---|
| honest | oracle | oracle | stock | 1.0 | Baseline works |
| impersonate v1 | nop | impersonator | stock | 0.0 | Tested seat ends at once; play cancels the cheater mid-post, and p1 leaves no record file |
| impersonate v2 | sleeper (record file + sleep 8s, no work) | impersonator (posts as p1 and p2) | stock | **1.0** | False attribution: tested seat did zero task work |
| impersonate v2 | sleeper | impersonator | token-auth fix (`e0a585d0`) | 0.0 | Fix kills this attack |
| honest | oracle | oracle | token-auth fix | 1.0 | Fix keeps the honest path |
| lazy-support | oracle | nop, 0.05x timeout | stock | 0.0 | p1 `AgentTimeoutError` after 30.0 s; the score measures p2's cooperation too |

Two holes compound in v2: the world trusts a caller-supplied `author`, and
the stock verifier trusts a non-empty `oracle.txt`/`trajectory.json` in the
seat's own logs as proof the seat ran. Either alone is not enough here.

Start skew in the honest arm was about 3 ms between seats for scripted
agents. That says nothing about model agents, whose setup and first turn
dominate. No start barrier exists; task authors must not assume fairness.

## First scored model trials

`terminus-2` + `openrouter/openai/gpt-oss-120b` in both seats, stock task,
3 attempts, 0.5x timeout: **3/3 reward 1.0**, 0 exceptions, total cost
$0.0028 (10,052 input + 7,048 output tokens across 6 seat runs). Per-seat
cost ranged $0.00025–$0.00078. The job summary names one agent
(`terminus-2 • openai/gpt-oss-120b`, the first tested seat); per-seat detail
exists only in `seat_results`.

Cross-check: Eval Lab `evallab jobs verify` on this job prints PASS
(trials 3/3). It recomputes rewards from trial files but reads no seat
fields, so a seat-level error beside reward 1 would pass that gate today.

## Reproduce

```bash
uv run python experiments/seat-attribution/run_arms.py
# against a fixed task copy:
SEAT_TASK=/path/to/hello-seats uv run python experiments/seat-attribution/run_arms.py
```

Model job config used for the scored row is `model-job.yaml` beside this
file; it needs `OPENROUTER_API_KEY` (`keys run -- uv run harbor run -c ...`
in Eval Lab terms). Expected spend is under $0.01 for 3 attempts.

## Addendum: model on the token-auth task (same day)

Same model setup, 1 attempt on the fixed task at `e0a585d0`: reward 0.0,
cost $0.0009. p2 posted correctly. p1 never posted: its polling script used
`jq`, which the player image does not install, the loop aborted, and the
model stopped after 3 steps. The reply-to workflow needs JSON parsing in
the seat; the image ships python3 and curl but no jq. Task authors should
treat seat-image affordances as part of the measured construct.

Token leakage check: no token value (32-hex) appears in the collected
`seats/*/agent/` artifacts of this run. The model read the token into a
shell variable (`token=$(cat /run/seat/token)`) and never echoed it. The
channel still exists — instructions and the oracle inline
`Authorization: Bearer <token>`, trajectories record commands and outputs,
and host-side scrubbing cannot know a world-issued secret — but it was not
exercised by this model on this attempt.
