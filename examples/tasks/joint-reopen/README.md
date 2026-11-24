# Joint Reopen

Two Harbor seats must decide whether to keep a provisional meeting. Both seats endorse the meeting before the world releases new calendars. If both can still attend, they must keep it. If either cannot, they must find a different slot that works for both. A seat that can still attend does not know whether its teammate can.

Use this task to test a specific behavior: **does a team revise a joint decision when private evidence requires it, without revising a good decision?** It does not measure whether teams form an agreement spontaneously. The first phase stages that agreement on purpose.

## Play

1. `east` and `west` independently endorse the same provisional slot with their own seat tokens.
2. Each seat can request its own updated card. Each posts one short report or passes. Reports need not be accurate.
3. Both get the same opportunity to request the other seat's report. Each casts one sealed vote. Neither can see the other's vote.

| Setting in the world | Values | What varies |
|---|---|---|
| `JR_SEED` | `0` to `2147483647` | The slot labels, not the rules. |
| `JR_FIXTURE` | `none`, `east`, `west`, `both` | Which seat can no longer attend the provisional meeting. |
| `JR_CHANNEL` | `deliver`, `withhold` | Whether a seat that requests the other report receives its contents. |

The private cards do not change between channel arms for a given seed and fixture. Withholding a report does not suppress the sender's action or change the phase barrier. The response tells the recipient that the report is unavailable. Do not describe this as an intervention on attention alone.

## Grade and evidence

The team earns 1 if its two final votes agree on the preferred feasible slot. A different but feasible slot scores 0 when the original booking still works. A split vote or an unfinished team also scores 0. These are different outcomes. A verifier failure is not a zero.

`verifier/joint-reopen.json` reports whether each role changed its vote, whether the final slot is feasible, and whether the team kept or repaired its booking. Its ordered timeline contains roles and action kinds, not slot labels. A card return or report return proves only that the world served it. Check `peer_report_before_vote` before saying the report was available for that decision. A seat can request it after voting while the other seat has not finished.

A blank report is a pass, not a false claim. The native verifier can check whether a nonempty report matches the sender's card. It cannot tell whether a model used the information. It exports no card, report, or vote contents.

## Run it

Use the [native study commands](../../joint_reopen/README.md) for one team or disjoint sets of registered teams. The stock fork runner supports regular model participants. The supplied `oracle` controls use separate seat containers and no model calls. Native Docker isolation is required. Never give one seat the verifier's cut, the task's seed or fixture, or the other seat's token.
