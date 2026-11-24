# Which model reporters does a four-seat scheduler actually need?

## Question and registration before new trials

Can an outside operator safely leave a reporting model idle when the delivered reports already determine the stock answer? The booker uses the same local, fail-closed calculation in both arms. We ask whether this saves actual reporter sessions without losing any native reward. The prior studies tested early stopping with a **model** booker and local recovery with **all** reporters. Neither tested their composition.

Use the next eight previously unscreened four-seat Flotilla scheduling worlds, indices 1232–1239. Do not inspect their calendars or choose cases by expected savings. Use the same stock generator `71ddf40b14ea81dd8eb0c32663e11abfe21cd1e3`, seed `1709000311`, easy/talk, 600 seconds, and rotating physical booker. None of these worlds is among the earlier 32 calendar screens. The question is about this scheduling contract, not arbitrary planning.

- Each of three independently managed reporter listeners starts a fresh tool-free personal subscription session **only if asked**. Use the same pinned client, requested model and zero paid-credit fallback as the original trials. Never substitute a correct local report for a model answer. The stock sender attaches its `exact` bit only after comparing the answer with its own starts.
- The booker is a local program in both arms. It gets only its own calendar and reports actually delivered. `all` requires all three expected exact reports in order. `stop` lets it book earlier, only when the received prefix consists of exact reports and the intersection is a singleton. The stock task guarantees there is one feasible booking. An inexact, ambiguous or out-of-order prefix cannot authorize a booking. An omitted reporter does not cast a vote. Neither arm starts a booking model or reads another seat's private calendar.
- Run both arms on all eight worlds. Run `all` first on even worlds and `stop` first on odd worlds. Keep every first attempt, including an incomplete one; no retries or substitutions. Record issued, answered and genuinely skipped reporting calls separately. Confirm actual subscription sessions and tokens by matching independent owner receipts to the native wire. A missing provider receipt does not mean zero usage. Record native grade, nulls, submissions and stock identity separately.
- Before the model teams, run three native controls with the same outside-owner connector. World 1232 with all truthful typed reporters must grade 1 in both arms. On 1233, make the first reporter in `stop` give an inexact list. The booker must refuse, make no submission and receive native grade 0. Offline wrong-order and ambiguous inputs must also refuse. Typed controls use no model sessions.

**Pass test:** all three controls match; all 16 issued native teams have bound first outcomes; both arms book correctly on all eight worlds; `stop` saves at least two confirmed reporter sessions across at least two distinct worlds. A requested reporter without a usable private receipt, or a missing native grade, makes the model comparison inconclusive. Accounted wrong grades or too few savings fail. Include any failure and its original grade. No claim about non-inferiority, spending, outage rate or unrelated tasks follows from a pass. Do not issue another paid or fallback-credit call for this study.

## Result: two reporters stayed idle, but the team did not use fewer tokens

The registered test passed at its stated minimum. Both arms booked correctly in all 8 matched worlds. `stop` avoided one real reporter session on world 1232 and one on 1239. The two workers used disjoint worlds and kept the registered order within every pair. No attempt was repeated. Each requested reporter answered. The 46 independent owner receipts matched the prompts and answers on the native wire.

| Eight native teams per arm | Correct bookings | Model reporting sessions | Client-reported tokens |
|---|---:|---:|---:|
| Ask every reporter | 8 | 24 | 138,305 |
| Stop on a certified answer | 8 | 22 | 143,817 |

The early-stop teams used **5,512 more** client-reported tokens in this sample. A reduction in calls did not establish a reduction in tokens or cost. These were tool-free personal subscription sessions. The client requested `gemini-4-carbon-low`. There was no paid-API key or paid-credit fallback. The provider did not attest the served model. The local booker started no model in either arm. This is a result about 8 calendars with a known unique answer, not about general team planning.

Both truthful typed controls scored 1. In the third control the first report was inexact. The other two were exact. The local booker refused; Harbor recorded no submission and a native grade of 0. The [redacted rows](results/certified-reporters/rows.json) contain the 3 controls and all 16 team outcomes. The [report](results/certified-reporters/panel.json) counts missing receipts separately from skipped reporters. The owner also checked the actual arm timestamps and which report was inexact against the private originals. The redacted rows alone do not show either fact.

### Secondary question: which reporting sessions account for the difference?

The total and both skipped sessions are already known. This is an exploratory accounting check, not a second registered experiment. Pair each requested reporter by calendar and physical seat. Separate tokens measured for calls absent in `stop` from changes among calls both teams made. Require the two terms to reproduce the observed total exactly. Count how often the total changes sign when one calendar is omitted. Do not infer a market price, provider bill or causal change in model reasoning from these receipts.

## Reproduce or collect new evidence

Rebuild the report with the standard library. Run this from the checkout root. No Docker, account or model call is necessary.

```sh
python3.12 -BS -m examples.seatlab.fanin.certified_panel \
  examples/seatlab/results/certified-reporters/rows.json
```

For a fresh native team, follow the [native setup](shared-read.md#run-the-same-comparison-on-eight-new-worlds). Set `HARBOR_PYTHON` and `DOCKER_SLOT`. Begin with a typed control:

```sh
PRIVATE=$(mktemp -d)
PRIVATE=$(cd "$PRIVATE" && pwd -P)
python3.12 -BS -m examples.seatlab.fanin.reporter_worker \
  --world 1240 --arm stop --out "$PRIVATE/w1240-control" \
  --harbor-python "$HARBOR_PYTHON" --slot "$DOCKER_SLOT" --control correct
```

For real reporters, use a new output directory and replace `--control correct` with `--agy "$AGY"`. `AGY` must be the absolute path to the supported, signed-in personal client. This is not an API-key interface. The worker acquires its slot before it starts the four listeners. An output directory is a permanent first attempt. Reusing one, even after interruption, is an error.

`external.json` records what the four programs sent and how Harbor graded their team. Only a personal run also writes `provenance.json`, which binds the nominated reporters to their separate client receipts. Keep the native directory, private logs and owner receipts private. The generator creates fresh local service credentials for each trial. Its native package checksum may differ across arms. The generator commit, seed and calendar digest identify the matched world.
