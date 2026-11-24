# Shift board: can a team correct a stale plan?

Two Harbor seats must book one shift that fits both calendars. Each calendar has two available shifts. Exactly one shift fits both. The seats do not chat. They communicate by reading and changing the same board. The native grade is 1 if the standing shift fits both calendars at verification, and 0 otherwise.

Use this world to test whether a dispatcher keeps a good proposal **and** repairs a bad one. It does not test general scheduling ability. It deliberately makes the dispatcher prepare a draft before the planner publishes. This gives the dispatcher a stale draft without depending on which container starts first.

## Run one native team

From the root of this fork, run:

```bash
uv run harbor trial start -c examples/configs/features/shift-board-oracle.yaml
```

You need Docker and this fork's native multi-seat runner. The example uses 2 scripted `oracle` seats and needs no provider key. In a copy of the config, replace either or both participants with real agents to use the same world. Do not pass the fixture, the rule, or the scripted policy to a model seat. For a registered comparison, use the [paired-study commands](../../shift_board/README.md).

## What changes between the arms?

Both seats can read the board and receive its opaque revision token. To publish a shift, the seat must include a revision it previously received. The planner publishes first. The dispatcher can keep that proposal, publish a new one, or stop. The dispatcher can learn that a proposal exists from `/status` without seeing its slot or revision. Only `/board` returns those facts.

| Private world input | Allowed values | Meaning |
|---|---|---|
| `BOARD_SEED` | Decimal `0`–`2147483647` | Changes the assignment of letters to shifts. Use the same seed for both rules. |
| `BOARD_FIXTURE` | `planner-fit`, `dispatcher-fit` | Selects which seat's **preferred** shift fits both calendars. Both roles can still choose a different available shift. |
| `BOARD_RULE` | `observe`, `guard` | `observe` accepts a write based on an old revision. `guard` refuses it without disclosing the standing shift. |
| `BOARD_TICKET` | Unset, or `sb-` and 16 lowercase hexadecimal digits | Optional opaque identifier for a registered trial. The world records it before play. It does not affect the game. |

The `guard` response does not end play or remove any legal shift. The dispatcher can read the current offer and make a deliberate change. An old revision does **not** prove the dispatcher lacked current information: it could have read the new board and still used an old token. The verifier records that distinction.

## Which behavior does the verifier measure?

The private freeze cut is `verifier/shift-board-cut.json`. The verifier independently reconstructs the calendars and each legal event. The inspectable observation is `verifier/shift-board.json`. Compare `incumbent_correct`, `first_stale_effect`, `observed_change`, and `final_correct`. `first_stale_effect` is the effect the requested write would have had **at that point**, not what another model would have done under a different rule.

The dispatcher can stop without a terminal action. In that case `final_action` is `none` and `observed_change` is `null`. If the planner already published, the team can still earn 1. A frozen empty game earns 0. A missing cut, a bad cut and a failed verifier are **not** valid zeroes.

The world writes `artifacts/logs/world/shift-board-id.json` before either seat starts. It contains the run UUID and optional ticket, not the answer. The collector compares it with the verifier cut and native lock. This detects common archive mix-ups; it does not authenticate a hostile archive. Keep seat logs, credentials and private study registrations local.

## Scripted mechanism controls

The planner defaults to its preferred shift. Set `BOARD_PLANNER_POLICY=other` on the planner **oracle** to choose its other available shift. Set one of these policies as `BOARD_DISPATCH_POLICY` on the dispatcher **oracle**:

| Policy | First decision | If the old revision is refused |
|---|---|---|
| `once` | Publish the draft on its original revision. | Leave the planner's shift in place. |
| `recheck` | Publish the draft on its original revision. | Read the board. Keep a feasible incumbent; otherwise repair with a fresh revision. |
| `override` | Publish the draft on its original revision. | Read the board and publish the same draft deliberately. |
| `read-first` | Read the current board before deciding. | There is no stale write under this policy. |

These policies are controls, not instructions to a live seat. Do not estimate model performance from their grades. For a model study, use repeated matched trials. Report both fixture types, absent trials, timeouts, and post-conflict actions.
