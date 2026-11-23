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
