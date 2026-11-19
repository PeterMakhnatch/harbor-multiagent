# A scheduling protocol with a hard round limit and an exact fallback

The earlier native study stopped the coordinator after 8 scalar rounds. That removed 20 of 24 otherwise feasible meetings. This study asks whether a team can keep a strict round limit without discarding an unresolved case. It does not study model ability. The 200 public MASBench scheduling cases and their original answers are already known.

## Registered arms

For all 200 public cases, compare 4 deterministic protocols in an independent offline implementation. Every seat knows only its own feasible meeting starts. The coordinator can use positive replies, not private calendars or reference answers. `cursor` continues scalar rounds until agreement or exhaustion. `budget8` ends unresolved cases with null after round 8. `bitset` requests 5 complete masks at bound 0 in 1 exchange. `hybrid8` uses the following rules:

1. Start at bound 0. Each of 5 seats returns its first local feasible start at or above that bound, or null.
2. Stop with null if any seat is exhausted. If all 5 agree, choose that tick. Otherwise, the maximum reply is the next lower bound.
3. After 8 **nonterminal** scalar rounds, request 5 suffix masks at the next lower bound. Bit 0 denotes that bound. Use fixed-width lowercase hex with `ceil((horizon - bound) / 4)` digits, leading zeros, and zero padding. AND the masks and choose the lowest set bit, or null. This is the only suffix round. A participant cannot send a second private calendar to another participant.

The round-9 request explicitly says `suffix-hex`. The broker may bind inputs and forward acknowledgments. Only `m1` chooses queries and the final answer. Only the verifier may compare against private calendars and the original reference. If any role fails to participate, that is not a successful match. This adaptation does not alter the original MASBench task.

## Native test and endpoints

Register exactly 24 new `hybrid8` native cells: case numbers 7 through 12 in each stratum (2, 3, 4, 5). These cases do not overlap the earlier 24 interactive cases. They are not blind to the prior offline full/first8 evaluation. Use the same 5 isolated nonroot seats, local Olam Docker runner, immutable reviewed task and new native trial UUIDs. Freeze the code before launching. A failed, invalid or incomplete attempted cell stays in the denominator; do not replace it. Report absence separately from an accepted null. Keep all outcomes, not just successful meetings.

The primary question is whether native choices and resource counts agree with independent per-case computations. Secondary endpoints are original reference agreement, physical feasibility, number of suffix switches, rounds and transmitted body bytes. Report all 4 offline arms on identical cases. For the native panel, report the other arms as **offline** comparisons unless separately run. No latency or language-model claim follows from a round or byte difference.

For each scalar exchange count 5 compact JSON integers for the request bound and the compact JSON scalars for the 5 replies. For a suffix exchange count 5 compact JSON arrays `["suffix-hex", bound]` and 5 compact JSON **strings** for masks. These totals omit JSON envelopes, client receipts, filesystem traffic, retries and all other transport overhead. Count scalar disclosures and transmitted suffix bit positions separately: a mask discloses negative information too. These are not privacy guarantees. Include all 5 seats, including `m1`.

Hypothesis: the hybrid matches the complete intersection with at most 9 exchanges. It need not use fewer bytes than a one-round mask or fewer bytes than the unbounded cursor. This is not a search over cutoffs. The code, projections and attribution go only to the personal fork. No model or cloud charge is required.
