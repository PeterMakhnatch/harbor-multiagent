# Can a Harbor worker measure when one seat reads another seat’s work?

## First gate: use a real subscription seat in a native team

**Question.** Can a sales seat and a finance seat use an existing, signed-in subscription to complete one native business handoff without a paid model API or a preselected action? This is a transport test, not another pricing study.

**Fixed case.** Use the released Market Handoff target 1, `own` arm, native command mode. Use the Antigravity CLI with the requested model `gemini-4-carbon-low` for both turns. Use separate sessions and role working directories. Disable AI credits, the direct model provider and model tools. Pass only the public role prompt. Do not run code written by a seat. Do not retry a valid but unhelpful answer. End this run if the client offers an extra charge or the requested model is unavailable.

**Pass test.** Both seats must return a valid first choice to the native task. Require separate provider sessions and a model identity for both. State whether the service reports a served model or only echoes the client selection. Bind each input and answer to the two Harbor seat records. Finance must receive the actual choice made by sales before finance acts. The native verifier and the released stock payoff must agree for that choice. Count an incomplete trial separately. A successful container launch is not a result. This test does not establish the quality of either agent.

**Measured.** [Trial `544059c3`](results/wait-for-peer/market-subscription.json) passed. Sales chose option 1. Finance received that choice and chose option 3. The stock payoff and native verifier both gave `0.1879657612700142`. The owner verified 2 distinct sessions, 2 first-answer hashes and 2 exact role inputs. The CLI reported 24,550 tokens and no tool actions. It echoed the requested model but did **not** attest the served model. Neither role received an API key. AI credit fallback was disabled. This is evidence about running external seats, not about this model's business skill.

## Does a reminder improve on an optional peer read?

**Operator decision.** Should the harness remind an agent to open a teammate's report, or actually deliver the report before the final decision? A real sender may publish an incomplete report. Report receipt and correct booking are different outcomes.

Use 8 fresh Flotilla `schedule` worlds, instances 408–415, 2 seats, `easy`, `talk`, stock generator `71ddf40b14ea81dd8eb0c32663e11abfe21cd1e3`, seed `579409134`, game timeout 600 seconds. The sender is `p1` in even worlds and `p2` in odd worlds. Each seat sees exactly the valid 2-hour start IDs on **its own** calendar. Neither sees the stock answer. No program written by a model runs. There is one correct joint booking in each world.

The [calendar manifest](results/wait-for-peer/calendar-registration.json) pins the world and stock semantics separately. Its SHA-256 is `bdf26d746f16a27e3b5120cf94c8af48a03f7df8ec56ab43c8431badcdf18bbc`. The archive contains no seat tokens or calendar answers.

**Three native teams per world.** A new sender submits its own list. The task sends a ready notice with no list. A new receiver chooses `open` or `skip`. The sender returns the report or a content-free acknowledgement over the stock message channel. The receiver then books 1 slot. Use a fresh `gemini-4-carbon-low` session for each of the 3 model turns. Do not repair the first answer. Do not share sender replies across teams. Keep incomplete attempts with no grade; never replace them.

| Arm | What the receiver sees before choosing whether to open | What the final receiver receives |
|---|---|---|
| `neutral` | The read offer and a neutral sentence about slot numbering | The report only on request |
| `remind` | The same offer, with a sentence about checking the peer's report before booking | The report only on request |
| `auto` | The same text as `neutral` | The report whether requested or not |

Keep the final prompt and call limits identical for identical delivered information. The operator must verify all 3 stock messages, the actual model inputs, the receiver's choice, and the stock reward. Rotate arm order through all 6 permutations in lexical order of `auto`, `neutral`, `remind`, indexed by `(instance − 408) mod 6`. Never start two trials on the same world at once.

**Pass test.** Treat the reminder as useful here only if all 24 teams are measured, it adds at least 2 opens in 8 worlds, and it adds at least 1 correct native booking over `neutral`. Report differences paired by world, including ties and losses. Report `auto` separately: it is a positive control for delivery, not a guarantee of a good decision. For a claim that automatic delivery helped on this panel, require correct delivery in 8/8 `auto` teams and at least 2 more correct bookings than `neutral`. Report partial coverage without choosing an arm if the study stops. Eight worlds do not support a claim about every model. Require a verified correct and wrong native control before starting this cohort.

### Result: the neutral teams already opened every report

All 24 teams finished on their first attempt. Neither reminder text nor automatic delivery improved the native reward. There was no missed read left to fix on these calendars. This result does **not** show that a reminder is useless when a team must handle an out-of-date report, a read cost, or a tight deadline.

| Arm | Teams | Sender reported its exact feasible starts | Receiver requested report | Report delivered before final decision | Correct native booking |
|---|---:|---:|---:|---:|---:|
| Neutral offer | 8 | 8 | 8 | 8 | 8 |
| Reminder | 8 | 8 | 8 | 8 | 8 |
| Automatic delivery | 8 | 8 | 8 | 8 | 8 |

Every paired comparison had 8 ties. The forced-delivery control still delivered the report when the typed receiver **did not** request it; that receiver booked a wrong slot and scored 0. The optional no-request control did not deliver the report and also scored 0. The positive control requested, received, booked correctly and scored 1. These checks separate a working delivery mechanism from a useful intervention.

The owner independently reconstructed the 72 first inputs and replies and the 24 grades from the native trials. It found 72 different session commitments, no reported tool actions, and no payment fallback. The client reported 499,001 tokens. No direct model API or paid container ran. The service did not attest which model served the requests. Do not treat the token count as a cash bill. The 24 native trials used 18.2 team-minutes. The first start to last finish took 9.9 minutes with at most 2 local Docker trials.

The [public rows](results/wait-for-peer/calendar-subscription-rows.json) contain the 24 checked trials and 3 separate controls. They do not include private owner requests or server session IDs. Rebuild the [paired decision](results/wait-for-peer/calendar-panel.json) without Docker, a subscription, or a model key:

```sh
python3.12 -BS -m examples.seatlab.wait_peer.panel \
  examples/seatlab/results/wait-for-peer/calendar-subscription-rows.json \
  | cmp - examples/seatlab/results/wait-for-peer/calendar-panel.json
```

## Run one team on your own machine

Use a checkout of this fork, Python 3.12, the fork's Harbor environment, and a local Docker daemon. Run the commands from the checkout root. The stock generator lives in the fork's history. The generated tasks and private trial outputs belong outside the checkout.

```sh
set -eu
umask 077
cache=$(cd "$(mktemp -d)" && pwd -P)
stock=71ddf40b14ea81dd8eb0c32663e11abfe21cd1e3
git archive "$stock" flotilla | tar -x -C "$cache"
python3.12 -B "$cache/flotilla/build_tasks.py" --family schedule --seats 2 \
  --difficulty easy --conditions talk --instances 408-415 --seed 579409134 \
  --play-timeout 600 --out "$cache/tasks"
tasks="$cache/tasks/schedule"
manifest="$(pwd -P)/examples/seatlab/results/wait-for-peer/calendar-registration.json"
digest=bdf26d746f16a27e3b5120cf94c8af48a03f7df8ec56ab43c8431badcdf18bbc
```

Set `HARBOR_PYTHON` to the fork environment's absolute Python path. Set `DOCKER_SLOT` to your site's executable admission wrapper. It receives the native command and arguments. It must hold a slot until the command exits. Do not use an unbounded wrapper on a shared Docker host.

Run this typed control first. It spends no model credits. A correct receiver on world 408 must earn a stock reward of 1. To check the negative path, use `--control wrong` and a new output directory. A wrong booking must earn 0.

```sh
python3.12 -BS -m examples.seatlab.wait_peer.worker "$tasks" "$manifest" \
  --sha256 "$digest" --world 408 --arm neutral --out "$cache/control-correct" \
  --slot "$DOCKER_SLOT" --harbor-python "$HARBOR_PYTHON" --control correct
```

The worker leaves `receipt.json` in the output directory. It will not reuse that directory. For a subscription run, use a new output directory and replace `--control correct` with `--agy "$AGY"`. `AGY` must be an absolute path to the supported signed-in CLI. The registered adapter checks version 1.5.15, the personal plan, model quota, and disabled AI-credit fallback. It does not ask the operator to change account settings or supply an API key. Its 3 one-use owner sockets are private. The native task does not get the account directory. The worker keeps its private owner records outside the repository. Publish the checked receipts, not the raw owner or Harbor directories.

## Let independent workers continue the same campaign

Use `campaign one` instead of choosing the next world yourself. Each invocation can issue **one** of the 24 registered first attempts. Give each worker the same private directory on the same machine. The study still requires the external Docker slot wrapper; the campaign lock is not a host-wide Docker limit.

```sh
campaign="$cache/my-control-campaign"
python3.12 -BS -m examples.seatlab.wait_peer.campaign one "$campaign" \
  --tasks "$tasks" --manifest "$manifest" --sha256 "$digest" \
  --slot "$DOCKER_SLOT" --harbor-python "$HARBOR_PYTHON" --control correct
python3.12 -BS -m examples.seatlab.wait_peer.campaign status "$campaign"
```

Run the same `one` command from a second shell to take an available world. Both shells must use the same absolute paths. Each world follows its registered arm order. To assign fixed parts to 2 workers, add `--shard 0/2` or `--shard 1/2`. Use a different campaign directory for live teams or negative controls. A mode change in an existing campaign is refused.

The owner publishes a permanent `.issued` record **before** starting a trial. A stopped or incomplete first attempt blocks new arms on that world. Do not remove the record to make the counts look complete. Other worlds remain eligible. `status` shows completed arms, unresolved worlds, and the next eligible arm for each world. Exit 3 from `one` means the worker could not claim work now; inspect `status`, not the exit code, to decide whether the study is complete. This design prevents duplicate issuance. It does not promise that every issued attempt will finish.

**Checked with real processes.** Two simultaneous invocations took worlds 408 and 409. Both native controls completed. A later invocation on shard `0/8` took the next arm of world 408 and earned another stock grade of 1. In separate campaigns we stopped the owner after issuance, once before the child started and once after Harbor's native configuration existed. Both worlds stayed unresolved. Neither could be issued again, and neither interruption was counted as a completed team. These checks used no model turns.

## Run the same comparison on eight new worlds

Choose the seed and eight consecutive indices **before** you inspect any model output. Make a new private directory. In the stock generation command above, replace `--instances 408-415 --seed 579409134` with, for example, `--instances 832-839 --seed 124331903`. Do not reuse old task or campaign directories. Keep the `schedule/easy/talk`, two-seat and timeout settings unchanged.

```sh
manifest="$cache/operator-calendar.json"
python3.12 -BS -m examples.seatlab.calendar_notices.transfer_registration freeze \
  "$tasks" --start 832 --count 8 --seed 124331903 > "$manifest"
digest=$(shasum -a 256 "$manifest" | cut -d' ' -f1)
```

Pass this manifest and digest to `worker` or `campaign one`. Each new campaign verifies all eight local worlds. It also regenerates the manifest from the pinned stock Git object before issuing a first attempt. A matching checksum from a modified generator is not enough. The campaign stores its own read-only copy of the manifest. `campaign status` does not need Git, Docker, or access to the original task directory. A completed or interrupted first attempt cannot be reassigned by changing the seed.

Use a new directory and `--agy "$AGY"` for live teams. To summarize their receipts without copying them by hand:

```sh
python3.12 -BS -m examples.seatlab.wait_peer.panel \
  --from-campaign "$cache/my-live-campaign" --manifest "$manifest" --sha256 "$digest"
```

The panel marks an operator cohort as exploratory, even if every team completes. It lists missing pairs rather than assigning them zero reward. A successful control does not count as a live team. Pass a separate `--control-receipt FILE` for each matching control you want to include. The published cohort still works with the original command.

**Observed on the new cohort.** A signed-in two-seat team booked world 832 correctly. Its three distinct first sessions reported 25,688 tokens. The three typed controls earned 1, 0 and 0. A second worker took the next authorized arm of the negative-control campaign. The [panel](results/wait-for-peer/operator-example/calendar-panel.json) reports one live result, 23 missing cells, all three controls verified, and **no conclusion about an intervention**.

```sh
d=examples/seatlab/results/wait-for-peer/operator-example
python3.12 -BS -m examples.seatlab.wait_peer.panel "$d/calendar-rows.json" \
  --manifest "$d/calendar-registration.json" \
  --sha256 928d2a53d733ee01e8a200508db1fa6ef164a933aadb81672bd9d65154138f12 \
  | cmp - "$d/calendar-panel.json"
```

The owner also replayed the 24 original live trials and 3 original controls from their native files. All 27 receipts matched their published bytes. A wrong digest, swapped stock commitments, and a missing eighth world each failed before any attempt was issued. Copying an authentic receipt into a directory without its issue record did not make it part of a campaign.
