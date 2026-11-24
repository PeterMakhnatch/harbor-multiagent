# Divide independent four-seat teams between operators

## Question and test before any campaign launch

Can two processes collect paired first attempts on fresh stock calendars without issuing the same team twice? Can a replacement operator distinguish an interrupted attempt from a world nobody started? The existing two-seat campaign cannot issue these four-seat teams. A single worker prevents reuse of its own output, but does not select work for the next process.

Freeze a local control cohort beginning at 1244 with 2 worlds. Give two processes the same registration and root. Require 4 distinct native trials, one per `(world, arm)`, each with a stock grade. The second arm of each world must start only after the first reports. Give both processes overlapping eligibility. The next call must return no work and leave every prior receipt in place.

On a separate private registration beginning at 1246, interrupt the first process after it publishes an issue. Require a replacement to leave the original issue untouched, collect a different world and report the stranded world by name. Do not automatically retry. Invalid or conflicting registration, a wrong digest and a reporter mode that disagrees with the registration must not start a team. These checks use typed controls, not language-model performance estimates.

The trusted unit is one workstation, one user and one private filesystem. This is an operator for a fixed campaign, not a distributed lock service or a daemon. The stock simulator, Harbor verifier and the one-team runner do not change.

## Separate personal-client acceptance check

Can a second invocation use an earlier model team's actual native and client receipts to unlock the other arm? On the previously unused world 1250, register `personal` and run each arm once in order. Use the existing personal subscription reporter, never an API key or paid-credit fallback. Require two separately graded native teams, distinct native IDs and matching owner receipts for every requested reporter. A confirmed skip must have neither a client request nor an owner record. If the client refuses or any required receipt is missing, retain the attempt and mark this check inconclusive. Do not replace it. This is a two-cell operator check, not another estimate of a policy effect.

## Negative grade acceptance check

A finished wrong team must still unlock the next arm. On world 1252, register `inexact` and issue the two arms once in order. The first arm asks all three reporters. Its first reporter must send an inexact list. Require no submission, a native grade of 0, a reported state and an eligible second arm. Keep the second arm's actual grade. A missing grade must not count as 0. This typed control starts no model.

## Start two independent operators

Use the [one-team setup](certified-reporters.md#reproduce-or-collect-new-evidence) first. Run the following commands from the checkout root on one workstation:

```sh
queue() { python3.12 -BS -m examples.seatlab.fanin.reporter_queue "$@"; }
PRIVATE=$(mktemp -d)
PRIVATE=$(cd "$PRIVATE" && pwd -P)
CAMP="$PRIVATE/campaign"
REG=$(queue init "$CAMP" --first 1260 --count 4 --mode correct)
HASH=$(printf '%s\n' "$REG" | python3.12 -c 'import json,sys; print(json.load(sys.stdin)["registration"]["sha256"])')
queue status "$CAMP" --sha256 "$HASH"
```

Give each operator the **same** `CAMP`, `HASH`, `HARBOR_PYTHON`, `DOCKER_SLOT` and checkout. Both can run this exact command. It claims at most one team:

```sh
python3.12 -BS -m examples.seatlab.fanin.reporter_queue one "$CAMP" \
  --sha256 "$HASH" --harbor-python "$HARBOR_PYTHON" \
  --slot "$DOCKER_SLOT" --control correct
```

Call `one` again to continue. For this 4-world example, each operator needs at most 8 calls. A worker may optionally select `--shard 0/2` or `--shard 1/2` to divide worlds instead of competing for the same ones. Use `status` to see both arms and native grades. It reads the private on-disk evidence again; it does not launch Harbor or query a provider.

| `one` exit | What the operator does |
|---:|---|
| 0 | A native grade exists. Collect the next cell. The grade can be 0. |
| 1 | The attempt started but lacks a verified grade. Keep its directory. Other worlds may still be available. |
| 2 | The inputs were refused. Correct the arguments before starting work. |
| 3 | No cell in this shard is available now. Inspect `status`; the work may be finished, busy or blocked. |

`one` and `status` report `incomplete_reason` separately from the native grade. For example, a real team scored 0 after its booker timed out. Its status was `reported`, its `incomplete_reason` was `p4:inbox_timeout`, and its owner usage was unverified. `p4` names the seat that timed out; it does not identify what happened inside another seat's model client. In contrast, the interrupted first attempt on 1246 has no grade or verified reason. `status.counts.incomplete` counts reported cells with a recorded reason, not all unresolved attempts. Neither command retries a team to clear that field.

For real reporters, register a **new** root with `--mode personal`. Pass `--agy "$AGY"` to each `one` call instead of `--control`. The site wrapper limits the number of teams. A new reporter session starts only after its team acquires a slot and requests that seat's decision. There is no unattended billing permission, paid fallback, cross-host lock, retry or lease expiry. Keep the campaign root private; use the redacted rows when sharing a study.

## What the acceptance checks show

- Two operators finished four separate native teams on calendars 1244 and 1245. They started on different calendars less than 1 millisecond apart. Each calendar ran its registered arms in order. All four stock grades were 1. No team ran twice.
- I interrupted a fifth worker on calendar 1246 after it obtained the Docker slot. Its grade stayed absent. A replacement finished a team on calendar 1247 without issuing calendar 1246 again. The first issue file did not change. This deliberate stop did not test a host power failure.
- Two real four-seat teams completed calendar 1250. Both earned 1. The all-report arm used 3 reporter sessions and 6,268 client tokens. The certified-stop arm used 3 sessions and 25,785 client tokens. All 6 sessions matched the native socket records. Both teams used the same local booker. The client did not record the served model. This single world does not estimate an effect.
- Both inexact typed teams on calendar 1252 refused to submit. Both earned 0. The first 0 released the next arm. The interrupted team's absent grade did not.
- Two simultaneous incompatible registrations for one directory returned one success and one refusal. A finished campaign returned exit 3. These guarantees apply to cooperating operators with a private local directory, not to other hosts or a malicious local user.

The [redacted evidence](results/reporter-campaign/operator-proof.json) includes the native trial IDs, UTC times, stock grades and cell states. No prompt, reply, session identifier or host path is required to inspect it.

The separate [operator timeout record](results/market-handoff/independent-operator.json) includes the actual `one` summary and `status` fields for a grade of 0. These fields also work when inspecting an older campaign. Unknown or private reason strings are redacted, not used to change an otherwise verified grade.
