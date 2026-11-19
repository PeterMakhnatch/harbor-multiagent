# MASBench Scheduling on five native Harbor seats

## Question

How often can a team schedule a meeting if each member proposes only its first 8 available starts? How does that compare with full availability disclosure on the same private calendars?

This is a port of the **data and reference answers** from [BUPT-GAMMA/MASBench Scheduling](https://github.com/BUPT-GAMMA/MASBench/tree/c0301a650db95958924bb080192c09fef39389a5/Scheduling). The upstream study uses language-model negotiation. Our two deterministic protocols do not reproduce that study or rank models. They establish known controls for testing future agents. They also test whether a native Harbor task presents each member with the right private input and reaches the correct outcome.

## Registered dataset and measures

Use all 200 released cases at `c0301a650db95958924bb080192c09fef39389a5`. The case ID consists of the upstream stratum and case number. The source labels the strata by the number of possible meeting windows. Each case has 5 private calendars, 5 days, and a 20-minute meeting. Use the five `MemberN_final_schedule.json` files and the original `task_plans` reference file. Do not use personal activity descriptions in a seat observation or shared result. Do not modify the upstream data, and do not select cases based on their outcomes.

For each case, independently derive each member's feasible starts and their intersection. Compare the complete intersection with the original reference starts, including both directions of disagreement. A discrepancy is a dataset or interpretation question, not permission to repair the answer key. Preserve every case in the denominator. Report any import errors separately.

- **Full:** each member submits all its feasible 20-minute starts. A designated member chooses the earliest start in the intersection of the five reports.
- **First 8:** each member sends its earliest 8 feasible starts, or all starts if fewer than 8 exist. The same member applies the same decision rule.
- If the reports have no common start, the leader records an explicit null decision. This is a completed but unresolved meeting. A missing report, leader decision or native trial is **not** a completed null decision.

Primary score: exact `(start, end)` membership in the original reference answers. Secondary score: whether each private calendar permits the decision. Measure starts reported per member as disclosure volume, not a privacy guarantee. One case, not each of its five members, is one paired observation. `first8` is a deliberately weak bounded-disclosure control. Its failure does not establish that all compact protocols fail.

## Execution and falsification

Run a deterministic data audit on all 200 paired cases. Estimate **no** native outcome from that audit. First exercise both treatments in Docker on case `1` of each of the 4 strata. Then run both treatments on the other 196 cases, subject to the owner’s stop instruction, available capacity and host health. Each native trial has 5 actual Harbor participants and 1 shared verifier reward. If a cell fails, preserve the first attempt. Keep repairs in a new output directory, with their own denominators. Numerical case order and deterministic disjoint shards allow independent workers to contribute without repeating cells.

Compare actual action, private view hashes, report contents and native reward against the audit. A scalar match is insufficient. Full may fail against the official key even if the decision is feasible; report that distinction. Do not silently replace an invalid measurement with the off-Docker prediction. Predeclared resource policy: local Docker, 1 native trial at a time in this lane, no model, no GPU, no paid provider. Do not prune shared Docker resources. Stop or yield to the fixed hourly control when the host is busy.