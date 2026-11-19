# Which parts of a coordinator view distinguish its recorded actions?

This is a secondary analysis of the 24 completed, unbounded native cursor runs. The cases and their outcomes are already public. This is not a blind benchmark. Register this split, field list, and abstention rule before computing any context-audit statistics. Do not run another world or call a model.

## Input and split

Use the verified source episode export from `export_episodes.py`, not the bounded target runs or the simulator. Require all 24 scheduled case IDs, one native UUID and one role episode per case. Use only acknowledged, pre-action coordinator views and accepted decisions. An accepted null is an action. A missing action is not one. Count every excluded frame. Do not use the decision, case ID, turn index, private answer, score, or future in the lookup key. The role name is always available.

Fit on cases 1 through 4 from each of the 4 scheduling strata (16 cases). Hold out cases 5 and 6 from each stratum (8 cases). Reject any shared native trial UUID or case digest across these sets. Use a single fixed split and report every registered representation, including those that never match across cases.

| Representation | Fields from the current acknowledged view |
| --- | --- |
| Full | Entire view |
| Role only | None |
| Round | `/round` |
| Request bound | `/bound` |
| Participant replies | `/replies` |
| Exchange | `/round`, `/bound`, `/replies` |

Missing is different from JSON null. The coordinator's initialization view has a different shape from a reply view. Do not insert defaults to make them look the same.

## Endpoints

Group fit frames by role and identical projected input. For each group, subtract the most frequent action count from its size. Sum the differences. This is the minimum number of recorded actions that **any deterministic current-view lookup** must disagree with on these observations. Compare with the same bound using the full view. Include repeated-class counts: a view unique to each frame can appear perfect by memorization.

For each held-out eligible frame, use just its projected input and the fit counts. Predict only for a unique most-frequent fit action. Report matches, disagreements, unseen inputs and tied inputs, with the total eligible denominator. Never update the lookup using held-out actions. This endpoint measures exact lookup transfer: an unseen key is an abstention, not proof that another algorithm cannot generalize.

This analysis tests how much of a recorded behavior the fields distinguish. It does not test whether a field caused the actor's choice, whether a lookup finds better meetings, or whether observations save model tokens. The existing independent cursor implementation is a different, algorithmic control.
