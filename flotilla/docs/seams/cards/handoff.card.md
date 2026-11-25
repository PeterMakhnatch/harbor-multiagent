# EvalCard: handoff — Operate a print dispatch desk

**Seam:** A capped note carries unfinished work and unresolved evidence to a fresh context across shifts.
**Kit commit:** `8a90df7c001a56a5c9d050885f2011f553ff61cc`; variant: `default`.
**Arms:** solo, solo-seam, team, hint, twin, team-record, team-fullrecord, probe-hunt-control, probe-hunt-trap, probe-ref-control, probe-ref-trap. Levels: canary (canary), standard (standard), heavy (heavy).
**Sweep:** 24 instances × 3 schedule seeds. No model calls.
**Policies unavailable:** none.

## Policy matrix (mean / min / n; invalid count in parentheses)

| Level | Arm | Seats | careful | naive | blind | lone | lapse-10 | lapse-20 | lapse-30 | lapse-50 |
|---|---|---|---|---|---|---|---|---|---|---|
| canary | solo | s1 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| canary | solo-seam | s1, s2, s3 | 1.000 / 1.000 / 72 (0) | 0.500 / 0.500 / 72 (0) | 0.432 / 0.333 / 72 (0) | 0.333 / 0.333 / 72 (0) | 0.773 / 0.417 / 72 (0) | 0.635 / 0.375 / 72 (0) | 0.530 / 0.375 / 72 (0) | 0.403 / 0.333 / 72 (0) |
| canary | team | s1, s2, s3 | 1.000 / 1.000 / 72 (0) | 0.500 / 0.500 / 72 (0) | 0.432 / 0.333 / 72 (0) | 0.333 / 0.333 / 72 (0) | 0.773 / 0.417 / 72 (0) | 0.635 / 0.375 / 72 (0) | 0.530 / 0.375 / 72 (0) | 0.403 / 0.333 / 72 (0) |
| canary | hint | s1, s2, s3 | 1.000 / 1.000 / 72 (0) | 0.500 / 0.500 / 72 (0) | 0.432 / 0.333 / 72 (0) | 0.333 / 0.333 / 72 (0) | 0.773 / 0.417 / 72 (0) | 0.635 / 0.375 / 72 (0) | 0.530 / 0.375 / 72 (0) | 0.403 / 0.333 / 72 (0) |
| canary | twin | s1, s2, s3 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.432 / 0.333 / 72 (0) | 0.333 / 0.333 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| canary | team-record | s1, s2, s3 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.432 / 0.333 / 72 (0) | 0.333 / 0.333 / 72 (0) | 0.773 / 0.417 / 72 (0) | 0.635 / 0.375 / 72 (0) | 0.530 / 0.375 / 72 (0) | 0.403 / 0.333 / 72 (0) |
| canary | team-fullrecord | s1, s2, s3 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.432 / 0.333 / 72 (0) | 0.333 / 0.333 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| canary | probe-hunt-control | s1, s2, s3 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| canary | probe-hunt-trap | s1, s2, s3 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.875 / 0.000 / 72 (0) | 0.667 / 0.000 / 72 (0) | 0.458 / 0.000 / 72 (0) | 0.375 / 0.000 / 72 (0) |
| canary | probe-ref-control | s1, s2, s3 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| canary | probe-ref-trap | s1, s2, s3 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.833 / 0.000 / 72 (0) | 0.792 / 0.000 / 72 (0) | 0.417 / 0.000 / 72 (0) | 0.417 / 0.000 / 72 (0) |
| standard | solo | s1 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard | solo-seam | s1, s2, s3, s4 | 1.000 / 1.000 / 72 (0) | 0.375 / 0.375 / 72 (0) | 0.396 / 0.312 / 72 (0) | 0.312 / 0.312 / 72 (0) | 0.850 / 0.438 / 72 (0) | 0.637 / 0.406 / 72 (0) | 0.452 / 0.312 / 72 (0) | 0.406 / 0.312 / 72 (0) |
| standard | team | s1, s2, s3, s4 | 1.000 / 1.000 / 72 (0) | 0.375 / 0.375 / 72 (0) | 0.396 / 0.312 / 72 (0) | 0.312 / 0.312 / 72 (0) | 0.850 / 0.438 / 72 (0) | 0.637 / 0.406 / 72 (0) | 0.452 / 0.312 / 72 (0) | 0.406 / 0.312 / 72 (0) |
| standard | hint | s1, s2, s3, s4 | 1.000 / 1.000 / 72 (0) | 0.375 / 0.375 / 72 (0) | 0.396 / 0.312 / 72 (0) | 0.312 / 0.312 / 72 (0) | 0.850 / 0.438 / 72 (0) | 0.637 / 0.406 / 72 (0) | 0.452 / 0.312 / 72 (0) | 0.406 / 0.312 / 72 (0) |
| standard | twin | s1, s2, s3, s4 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.396 / 0.312 / 72 (0) | 0.312 / 0.312 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard | team-record | s1, s2, s3, s4 | 1.000 / 1.000 / 72 (0) | 0.812 / 0.375 / 72 (0) | 0.396 / 0.312 / 72 (0) | 0.312 / 0.312 / 72 (0) | 0.850 / 0.438 / 72 (0) | 0.637 / 0.406 / 72 (0) | 0.452 / 0.312 / 72 (0) | 0.406 / 0.312 / 72 (0) |
| standard | team-fullrecord | s1, s2, s3, s4 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.396 / 0.312 / 72 (0) | 0.312 / 0.312 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard | probe-hunt-control | s1, s2, s3, s4 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard | probe-hunt-trap | s1, s2, s3, s4 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.875 / 0.000 / 72 (0) | 0.625 / 0.000 / 72 (0) | 0.375 / 0.000 / 72 (0) | 0.125 / 0.000 / 72 (0) |
| standard | probe-ref-control | s1, s2, s3, s4 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard | probe-ref-trap | s1, s2, s3, s4 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.917 / 0.000 / 72 (0) | 0.792 / 0.000 / 72 (0) | 0.625 / 0.000 / 72 (0) | 0.542 / 0.000 / 72 (0) |
| heavy | solo | s1 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy | solo-seam | s1, s2, s3, s4, s5, s6, s7, s8 | 1.000 / 1.000 / 72 (0) | 0.312 / 0.312 / 72 (0) | 0.289 / 0.281 / 72 (0) | 0.281 / 0.281 / 72 (0) | 0.711 / 0.422 / 72 (0) | 0.573 / 0.359 / 72 (0) | 0.435 / 0.328 / 72 (0) | 0.328 / 0.297 / 72 (0) |
| heavy | team | s1, s2, s3, s4, s5, s6, s7, s8 | 1.000 / 1.000 / 72 (0) | 0.312 / 0.312 / 72 (0) | 0.289 / 0.281 / 72 (0) | 0.281 / 0.281 / 72 (0) | 0.711 / 0.422 / 72 (0) | 0.573 / 0.359 / 72 (0) | 0.435 / 0.328 / 72 (0) | 0.328 / 0.297 / 72 (0) |
| heavy | hint | s1, s2, s3, s4, s5, s6, s7, s8 | 1.000 / 1.000 / 72 (0) | 0.312 / 0.312 / 72 (0) | 0.289 / 0.281 / 72 (0) | 0.281 / 0.281 / 72 (0) | 0.711 / 0.422 / 72 (0) | 0.573 / 0.359 / 72 (0) | 0.435 / 0.328 / 72 (0) | 0.328 / 0.297 / 72 (0) |
| heavy | twin | s1, s2, s3, s4, s5, s6, s7, s8 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.289 / 0.281 / 72 (0) | 0.281 / 0.281 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy | team-record | s1, s2, s3, s4, s5, s6, s7, s8 | 1.000 / 1.000 / 72 (0) | 0.812 / 0.812 / 72 (0) | 0.289 / 0.281 / 72 (0) | 0.281 / 0.281 / 72 (0) | 0.711 / 0.422 / 72 (0) | 0.573 / 0.359 / 72 (0) | 0.435 / 0.328 / 72 (0) | 0.328 / 0.297 / 72 (0) |
| heavy | team-fullrecord | s1, s2, s3, s4, s5, s6, s7, s8 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.289 / 0.281 / 72 (0) | 0.281 / 0.281 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy | probe-hunt-control | s1, s2, s3, s4, s5, s6, s7, s8 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy | probe-hunt-trap | s1, s2, s3, s4, s5, s6, s7, s8 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.833 / 0.000 / 72 (0) | 0.750 / 0.000 / 72 (0) | 0.667 / 0.000 / 72 (0) | 0.458 / 0.000 / 72 (0) |
| heavy | probe-ref-control | s1, s2, s3, s4, s5, s6, s7, s8 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy | probe-ref-trap | s1, s2, s3, s4, s5, s6, s7, s8 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.875 / 0.000 / 72 (0) | 0.792 / 0.000 / 72 (0) | 0.583 / 0.000 / 72 (0) | 0.458 / 0.000 / 72 (0) |

## Checks

| Check | Standard | Result | Evidence |
|---|---|---|---|
| levels | A5 | **PASS** | All requested dose levels available |
| solvable | A2/B1 | **PASS** | 2376 runs; careful reward = 1 |
| easy alone | A2 | **PASS** | 216 runs; solo careful reward = 1 |
| floor | A3 | **NA** | No applicable policy/arm/level; every nop reward ≤ 0.05 |
| chance | A3 | **PASS** | canary/team/blind: actual 0.4322916666666667, expected 0.4375 (absolute tolerance 0.1); canary/probe-hunt-trap/blind: actual 0.0, expected 0.0000 (absolute tolerance 0.1); canary/probe-ref-trap/blind: actual 0.0, expected 0.0000 (absolute tolerance 0.1); standard/team/blind: actual 0.3958333333333333, expected 0.4062 (absolute tolerance 0.1); standard/probe-hunt-trap/blind: actual 0.0, expected… (full evidence in JSON) |
| separation | A4 | **PASS** | canary/probe-hunt-trap: gap 1.000; canary/probe-ref-trap: gap 1.000; canary/probe-hunt-control: naive min 1.0; canary/probe-ref-control: naive min 1.0; standard/probe-hunt-trap: gap 1.000; standard/probe-ref-trap: gap 1.000; standard/probe-hunt-control: naive min 1.0; standard/probe-ref-control: naive min 1.0; heavy/probe-hunt-trap: gap 1.000; heavy/probe-ref-trap: gap 1.000; heavy/probe-hunt-c… (full evidence in JSON) |
| dose response | A5/I1 | **PASS** | canary → standard → heavy: [0.5, 0.375, 0.3125] |
| one seat is not enough | A3/C1 | **PASS** | 216 runs; every lone team reward ≤ 0.5 |
| schedule independence | E3 | **PASS** | 2376 runs; careful scores 1 across 3 schedule seed(s); max within-instance naive spread: 0.0 |
| simulation validity | F1 | **PASS** | 19008 runs; No invalid runs in the ordinary sweep |
| determinism | E1/E2 | **PASS** | 66/66 sample event hashes match |
| no leak | C1/D2 | **PASS** | 792 built tasks; seat views and family QA scanned |
| pairs | A1/D2 | **PASS** | probe-hunt-control→probe-hunt-trap: 72 pairs, max 0 changed chars; fields: none; probe-ref-control→probe-ref-trap: 72 pairs, max 0 changed chars; fields: none; team→hint: 72 pairs, max 896 changed chars; fields: s1/instruction, s2/instruction, s3/instruction, s4/instruction, s5/instruction, s6/instruction, s7/instruction, s8/instruction; team→solo-seam: 72 pairs, max 976 changed chars; fields: … (full evidence in JSON) |
| twin rule | A1 | **PASS** | Every team/trap has a declared twin |
| invalid is not zero | F1 | **PASS** | Budget-limited simulation: stalled=True, invalid=True, reward=None |
| canary | A6 | **PASS** | Marker checked in 792 task files and all seat views |
| power plan | A3 | **PASS** | 36 paired comparisons; fixed exact McNemar plan (see table) |

## Three costs (paired reward differences)

Positive means the control scores higher than team; missing arms are NA.

| Level | Policy | Cost | Control − team | Effect | Valid pairs / instances | Excluded |
|---|---|---|---|---|---|---|
| canary | careful | boundary | team-fullrecord − team | +0.000 | 72 / 24 | 0 |
| canary | careful | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | careful | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | naive | boundary | team-fullrecord − team | +0.500 | 72 / 24 | 0 |
| canary | naive | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | naive | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | blind | boundary | team-fullrecord − team | +0.000 | 72 / 24 | 0 |
| canary | blind | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | blind | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | lone | boundary | team-fullrecord − team | +0.000 | 72 / 24 | 0 |
| canary | lone | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | lone | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-10 | boundary | team-fullrecord − team | +0.227 | 72 / 24 | 0 |
| canary | lapse-10 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-10 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-20 | boundary | team-fullrecord − team | +0.365 | 72 / 24 | 0 |
| canary | lapse-20 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-20 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-30 | boundary | team-fullrecord − team | +0.470 | 72 / 24 | 0 |
| canary | lapse-30 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-30 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-50 | boundary | team-fullrecord − team | +0.597 | 72 / 24 | 0 |
| canary | lapse-50 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-50 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | careful | boundary | team-fullrecord − team | +0.000 | 72 / 24 | 0 |
| standard | careful | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | careful | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | naive | boundary | team-fullrecord − team | +0.625 | 72 / 24 | 0 |
| standard | naive | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | naive | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | blind | boundary | team-fullrecord − team | +0.000 | 72 / 24 | 0 |
| standard | blind | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | blind | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | lone | boundary | team-fullrecord − team | +0.000 | 72 / 24 | 0 |
| standard | lone | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | lone | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-10 | boundary | team-fullrecord − team | +0.150 | 72 / 24 | 0 |
| standard | lapse-10 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-10 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-20 | boundary | team-fullrecord − team | +0.363 | 72 / 24 | 0 |
| standard | lapse-20 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-20 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-30 | boundary | team-fullrecord − team | +0.548 | 72 / 24 | 0 |
| standard | lapse-30 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-30 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-50 | boundary | team-fullrecord − team | +0.594 | 72 / 24 | 0 |
| standard | lapse-50 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-50 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | careful | boundary | team-fullrecord − team | +0.000 | 72 / 24 | 0 |
| heavy | careful | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | careful | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | naive | boundary | team-fullrecord − team | +0.688 | 72 / 24 | 0 |
| heavy | naive | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | naive | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | blind | boundary | team-fullrecord − team | +0.000 | 72 / 24 | 0 |
| heavy | blind | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | blind | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | lone | boundary | team-fullrecord − team | +0.000 | 72 / 24 | 0 |
| heavy | lone | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | lone | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | lapse-10 | boundary | team-fullrecord − team | +0.289 | 72 / 24 | 0 |
| heavy | lapse-10 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | lapse-10 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | lapse-20 | boundary | team-fullrecord − team | +0.427 | 72 / 24 | 0 |
| heavy | lapse-20 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | lapse-20 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | lapse-30 | boundary | team-fullrecord − team | +0.565 | 72 / 24 | 0 |
| heavy | lapse-30 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | lapse-30 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | lapse-50 | boundary | team-fullrecord − team | +0.672 | 72 / 24 | 0 |
| heavy | lapse-50 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | lapse-50 | protocol | hint − team | +0.000 | 72 / 24 | 0 |

## Power plan (scripted stand-ins)

Fixed sample, exact two-sided McNemar, α=0.05, target power 0.8; binary success means reward = 1. The paired reward effect also includes partial credit.
Schedule repeats estimate outcome probabilities; they are not independent instances. The planned n assumes independent new instances, one paired run each.
No-effect and NA cells have no planned n. Plans search up to 1,000 instances. These are sensitivity estimates, not model performance predictions.

| Level | Policy | Control − treatment | Reward effect | Success effect | + / − | Valid pairs / instances | Excluded | Planned n |
|---|---|---|---|---|---|---|---|---|
| canary | lapse-10 | probe-hunt-control − probe-hunt-trap | +0.125 | +0.125 | 9 / 0 | 72 / 24 | 0 | 62 |
| canary | lapse-10 | probe-ref-control − probe-ref-trap | +0.167 | +0.167 | 12 / 0 | 72 / 24 | 0 | 46 |
| canary | lapse-10 | team-fullrecord − team | +0.227 | +0.542 | 39 / 0 | 72 / 24 | 0 | 13 |
| canary | lapse-20 | probe-hunt-control − probe-hunt-trap | +0.333 | +0.333 | 24 / 0 | 72 / 24 | 0 | 23 |
| canary | lapse-20 | probe-ref-control − probe-ref-trap | +0.208 | +0.208 | 15 / 0 | 72 / 24 | 0 | 37 |
| canary | lapse-20 | team-fullrecord − team | +0.365 | +0.917 | 66 / 0 | 72 / 24 | 0 | 7 |
| canary | lapse-30 | probe-hunt-control − probe-hunt-trap | +0.542 | +0.542 | 39 / 0 | 72 / 24 | 0 | 13 |
| canary | lapse-30 | probe-ref-control − probe-ref-trap | +0.583 | +0.583 | 42 / 0 | 72 / 24 | 0 | 12 |
| canary | lapse-30 | team-fullrecord − team | +0.470 | +1.000 | 72 / 0 | 72 / 24 | 0 | 6 |
| canary | lapse-50 | probe-hunt-control − probe-hunt-trap | +0.625 | +0.625 | 45 / 0 | 72 / 24 | 0 | 11 |
| canary | lapse-50 | probe-ref-control − probe-ref-trap | +0.583 | +0.583 | 42 / 0 | 72 / 24 | 0 | 12 |
| canary | lapse-50 | team-fullrecord − team | +0.597 | +1.000 | 72 / 0 | 72 / 24 | 0 | 6 |
| standard | lapse-10 | probe-hunt-control − probe-hunt-trap | +0.125 | +0.125 | 9 / 0 | 72 / 24 | 0 | 62 |
| standard | lapse-10 | probe-ref-control − probe-ref-trap | +0.083 | +0.083 | 6 / 0 | 72 / 24 | 0 | 94 |
| standard | lapse-10 | team-fullrecord − team | +0.150 | +0.667 | 48 / 0 | 72 / 24 | 0 | 11 |
| standard | lapse-20 | probe-hunt-control − probe-hunt-trap | +0.375 | +0.375 | 27 / 0 | 72 / 24 | 0 | 20 |
| standard | lapse-20 | probe-ref-control − probe-ref-trap | +0.208 | +0.208 | 15 / 0 | 72 / 24 | 0 | 37 |
| standard | lapse-20 | team-fullrecord − team | +0.363 | +0.958 | 69 / 0 | 72 / 24 | 0 | 7 |
| standard | lapse-30 | probe-hunt-control − probe-hunt-trap | +0.625 | +0.625 | 45 / 0 | 72 / 24 | 0 | 11 |
| standard | lapse-30 | probe-ref-control − probe-ref-trap | +0.375 | +0.375 | 27 / 0 | 72 / 24 | 0 | 20 |
| standard | lapse-30 | team-fullrecord − team | +0.548 | +1.000 | 72 / 0 | 72 / 24 | 0 | 6 |
| standard | lapse-50 | probe-hunt-control − probe-hunt-trap | +0.875 | +0.875 | 63 / 0 | 72 / 24 | 0 | 8 |
| standard | lapse-50 | probe-ref-control − probe-ref-trap | +0.458 | +0.458 | 33 / 0 | 72 / 24 | 0 | 16 |
| standard | lapse-50 | team-fullrecord − team | +0.594 | +1.000 | 72 / 0 | 72 / 24 | 0 | 6 |
| heavy | lapse-10 | probe-hunt-control − probe-hunt-trap | +0.167 | +0.167 | 12 / 0 | 72 / 24 | 0 | 46 |
| heavy | lapse-10 | probe-ref-control − probe-ref-trap | +0.125 | +0.125 | 9 / 0 | 72 / 24 | 0 | 62 |
| heavy | lapse-10 | team-fullrecord − team | +0.289 | +0.958 | 69 / 0 | 72 / 24 | 0 | 7 |
| heavy | lapse-20 | probe-hunt-control − probe-hunt-trap | +0.250 | +0.250 | 18 / 0 | 72 / 24 | 0 | 31 |
| heavy | lapse-20 | probe-ref-control − probe-ref-trap | +0.208 | +0.208 | 15 / 0 | 72 / 24 | 0 | 37 |
| heavy | lapse-20 | team-fullrecord − team | +0.427 | +1.000 | 72 / 0 | 72 / 24 | 0 | 6 |
| heavy | lapse-30 | probe-hunt-control − probe-hunt-trap | +0.333 | +0.333 | 24 / 0 | 72 / 24 | 0 | 23 |
| heavy | lapse-30 | probe-ref-control − probe-ref-trap | +0.417 | +0.417 | 30 / 0 | 72 / 24 | 0 | 18 |
| heavy | lapse-30 | team-fullrecord − team | +0.565 | +1.000 | 72 / 0 | 72 / 24 | 0 | 6 |
| heavy | lapse-50 | probe-hunt-control − probe-hunt-trap | +0.542 | +0.542 | 39 / 0 | 72 / 24 | 0 | 13 |
| heavy | lapse-50 | probe-ref-control − probe-ref-trap | +0.542 | +0.542 | 39 / 0 | 72 / 24 | 0 | 13 |
| heavy | lapse-50 | team-fullrecord − team | +0.672 | +1.000 | 72 / 0 | 72 / 24 | 0 | 6 |

## Versions

| Task(s) | Simulator versions |
|---|---|
| canary/solo/0, canary/solo/1, canary/solo/10, canary/solo/11, canary/solo/12, canary/solo/13, canary/solo/14, canary/solo/15, canary/solo/16, canary/solo/17, canary/solo/18, canary/solo/19, canary/solo/2, canary/solo/20, canary/solo/21, canary/solo/22, canary/solo/23, canary/solo/3, canary/solo/4, canary/solo/5, canary/solo/6, canary/solo/7, canary/solo/8, canary/solo/9 | `{"extension": "92dbf2821472", "family": "d2d5a636506c", "family_version": 2, "instructions": {"s1": "6ccea7068811"}, "policies": "d85d3ec7f7d8", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo-seam/0, canary/solo-seam/1, canary/solo-seam/10, canary/solo-seam/11, canary/solo-seam/12, canary/solo-seam/13, canary/solo-seam/14, canary/solo-seam/15, canary/solo-seam/16, canary/solo-seam/17, canary/solo-seam/18, canary/solo-seam/19, canary/solo-seam/2, canary/solo-seam/20, canary/solo-seam/21, canary/solo-seam/22, canary/solo-seam/23, canary/solo-seam/3, canary/solo-seam/4, canary/solo-seam/5, canary/solo-seam/6, canary/solo-seam/7, canary/solo-seam/8, canary/solo-seam/9 | `{"extension": "92dbf2821472", "family": "d2d5a636506c", "family_version": 2, "instructions": {"s1": "fd1587616c7a", "s2": "a23656c2e051", "s3": "f3bc233e042d"}, "policies": "d85d3ec7f7d8", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/team/0, canary/team/1, canary/team/10, canary/team/11, canary/team/12, canary/team/13, canary/team/14, canary/team/15, canary/team/16, canary/team/17, canary/team/18, canary/team/19, canary/team/2, canary/team/20, canary/team/21, canary/team/22, canary/team/23, canary/team/3, canary/team/4, canary/team/5, canary/team/6, canary/team/7, canary/team/8, canary/team/9 | `{"extension": "92dbf2821472", "family": "d2d5a636506c", "family_version": 2, "instructions": {"s1": "78b6f201629f", "s2": "25496697cbc3", "s3": "8a7a61194dfc"}, "policies": "d85d3ec7f7d8", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/hint/0, canary/hint/1, canary/hint/10, canary/hint/11, canary/hint/12, canary/hint/13, canary/hint/14, canary/hint/15, canary/hint/16, canary/hint/17, canary/hint/18, canary/hint/19, canary/hint/2, canary/hint/20, canary/hint/21, canary/hint/22, canary/hint/23, canary/hint/3, canary/hint/4, canary/hint/5, canary/hint/6, canary/hint/7, canary/hint/8, canary/hint/9 | `{"extension": "92dbf2821472", "family": "d2d5a636506c", "family_version": 2, "instructions": {"s1": "c86434434d22", "s2": "5172de8ea7b8", "s3": "62896c9f2b06"}, "policies": "d85d3ec7f7d8", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/twin/0, canary/twin/1, canary/twin/10, canary/twin/11, canary/twin/12, canary/twin/13, canary/twin/14, canary/twin/15, canary/twin/16, canary/twin/17, canary/twin/18, canary/twin/19, canary/twin/2, canary/twin/20, canary/twin/21, canary/twin/22, canary/twin/23, canary/twin/3, canary/twin/4, canary/twin/5, canary/twin/6, canary/twin/7, canary/twin/8, canary/twin/9 | `{"extension": "92dbf2821472", "family": "d2d5a636506c", "family_version": 2, "instructions": {"s1": "c1e0068e8956", "s2": "56be46e0eca2", "s3": "d6e21df07e87"}, "policies": "d85d3ec7f7d8", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/team-record/0, canary/team-record/1, canary/team-record/10, canary/team-record/11, canary/team-record/12, canary/team-record/13, canary/team-record/14, canary/team-record/15, canary/team-record/16, canary/team-record/17, canary/team-record/18, canary/team-record/19, canary/team-record/2, canary/team-record/20, canary/team-record/21, canary/team-record/22, canary/team-record/23, canary/team-record/3, canary/team-record/4, canary/team-record/5, canary/team-record/6, canary/team-record/7, canary/team-record/8, canary/team-record/9 | `{"extension": "92dbf2821472", "family": "d2d5a636506c", "family_version": 2, "instructions": {"s1": "d3a7e1d4c203", "s2": "7cf699040050", "s3": "f1e16380ed95"}, "policies": "d85d3ec7f7d8", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/team-fullrecord/0, canary/team-fullrecord/1, canary/team-fullrecord/10, canary/team-fullrecord/11, canary/team-fullrecord/12, canary/team-fullrecord/13, canary/team-fullrecord/14, canary/team-fullrecord/15, canary/team-fullrecord/16, canary/team-fullrecord/17, canary/team-fullrecord/18, canary/team-fullrecord/19, canary/team-fullrecord/2, canary/team-fullrecord/20, canary/team-fullrecord/21, canary/team-fullrecord/22, canary/team-fullrecord/23, canary/team-fullrecord/3, canary/team-fullrecord/4, canary/team-fullrecord/5, canary/team-fullrecord/6, canary/team-fullrecord/7, canary/team-fullrecord/8, canary/team-fullrecord/9 | `{"extension": "92dbf2821472", "family": "d2d5a636506c", "family_version": 2, "instructions": {"s1": "d6c2ba69ae18", "s2": "754d00e16e47", "s3": "49b701796a41"}, "policies": "d85d3ec7f7d8", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/probe-hunt-control/0, canary/probe-hunt-control/1, canary/probe-hunt-control/10, canary/probe-hunt-control/11, canary/probe-hunt-control/12, canary/probe-hunt-control/13, canary/probe-hunt-control/14, canary/probe-hunt-control/15, canary/probe-hunt-control/16, canary/probe-hunt-control/17, canary/probe-hunt-control/18, canary/probe-hunt-control/19, canary/probe-hunt-control/2, canary/probe-hunt-control/20, canary/probe-hunt-control/21, canary/probe-hunt-control/22, canary/probe-hunt-control/23, canary/probe-hunt-control/3, canary/probe-hunt-control/4, canary/probe-hunt-control/5, canary/probe-hunt-control/6, canary/probe-hunt-control/7, canary/probe-hunt-control/8, canary/probe-hunt-control/9, canary/probe-hunt-trap/0, canary/probe-hunt-trap/1, canary/probe-hunt-trap/10, canary/probe-hunt-trap/11, canary/probe-hunt-trap/12, canary/probe-hunt-trap/13, canary/probe-hunt-trap/14, canary/probe-hunt-trap/15, canary/probe-hunt-trap/16, canary/probe-hunt-trap/17, canary/probe-hunt-trap/18, canary/probe-hunt-trap/19, canary/probe-hunt-trap/2, canary/probe-hunt-trap/20, canary/probe-hunt-trap/21, canary/probe-hunt-trap/22, canary/probe-hunt-trap/23, canary/probe-hunt-trap/3, canary/probe-hunt-trap/4, canary/probe-hunt-trap/5, canary/probe-hunt-trap/6, canary/probe-hunt-trap/7, canary/probe-hunt-trap/8, canary/probe-hunt-trap/9, canary/probe-ref-control/0, canary/probe-ref-control/1, canary/probe-ref-control/10, canary/probe-ref-control/11, canary/probe-ref-control/12, canary/probe-ref-control/13, canary/probe-ref-control/14, canary/probe-ref-control/15, canary/probe-ref-control/16, canary/probe-ref-control/17, canary/probe-ref-control/18, canary/probe-ref-control/19, canary/probe-ref-control/2, canary/probe-ref-control/20, canary/probe-ref-control/21, canary/probe-ref-control/22, canary/probe-ref-control/23, canary/probe-ref-control/3, canary/probe-ref-control/4, canary/probe-ref-control/5, canary/probe-ref-control/6, canary/probe-ref-control/7, canary/probe-ref-control/8, canary/probe-ref-control/9, canary/probe-ref-trap/0, canary/probe-ref-trap/1, canary/probe-ref-trap/10, canary/probe-ref-trap/11, canary/probe-ref-trap/12, canary/probe-ref-trap/13, canary/probe-ref-trap/14, canary/probe-ref-trap/15, canary/probe-ref-trap/16, canary/probe-ref-trap/17, canary/probe-ref-trap/18, canary/probe-ref-trap/19, canary/probe-ref-trap/2, canary/probe-ref-trap/20, canary/probe-ref-trap/21, canary/probe-ref-trap/22, canary/probe-ref-trap/23, canary/probe-ref-trap/3, canary/probe-ref-trap/4, canary/probe-ref-trap/5, canary/probe-ref-trap/6, canary/probe-ref-trap/7, canary/probe-ref-trap/8, canary/probe-ref-trap/9 | `{"extension": "92dbf2821472", "family": "d2d5a636506c", "family_version": 2, "instructions": {"s1": "905bf5f95b45", "s2": "25496697cbc3", "s3": "905bf5f95b45"}, "policies": "d85d3ec7f7d8", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo/0, standard/solo/1, standard/solo/10, standard/solo/11, standard/solo/12, standard/solo/13, standard/solo/14, standard/solo/15, standard/solo/16, standard/solo/17, standard/solo/18, standard/solo/19, standard/solo/2, standard/solo/20, standard/solo/21, standard/solo/22, standard/solo/23, standard/solo/3, standard/solo/4, standard/solo/5, standard/solo/6, standard/solo/7, standard/solo/8, standard/solo/9 | `{"extension": "92dbf2821472", "family": "d2d5a636506c", "family_version": 2, "instructions": {"s1": "f3a460c74bed"}, "policies": "d85d3ec7f7d8", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo-seam/0, standard/solo-seam/1, standard/solo-seam/10, standard/solo-seam/11, standard/solo-seam/12, standard/solo-seam/13, standard/solo-seam/14, standard/solo-seam/15, standard/solo-seam/16, standard/solo-seam/17, standard/solo-seam/18, standard/solo-seam/19, standard/solo-seam/2, standard/solo-seam/20, standard/solo-seam/21, standard/solo-seam/22, standard/solo-seam/23, standard/solo-seam/3, standard/solo-seam/4, standard/solo-seam/5, standard/solo-seam/6, standard/solo-seam/7, standard/solo-seam/8, standard/solo-seam/9 | `{"extension": "92dbf2821472", "family": "d2d5a636506c", "family_version": 2, "instructions": {"s1": "a51789e4bd5c", "s2": "a80e7a4a82a6", "s3": "80feccfabe0f", "s4": "4bdfa0026334"}, "policies": "d85d3ec7f7d8", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/team/0, standard/team/1, standard/team/10, standard/team/11, standard/team/12, standard/team/13, standard/team/14, standard/team/15, standard/team/16, standard/team/17, standard/team/18, standard/team/19, standard/team/2, standard/team/20, standard/team/21, standard/team/22, standard/team/23, standard/team/3, standard/team/4, standard/team/5, standard/team/6, standard/team/7, standard/team/8, standard/team/9 | `{"extension": "92dbf2821472", "family": "d2d5a636506c", "family_version": 2, "instructions": {"s1": "39a3553cbdb0", "s2": "1681b8e386ea", "s3": "b0cab75e3c12", "s4": "97bc7d84a23a"}, "policies": "d85d3ec7f7d8", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/hint/0, standard/hint/1, standard/hint/10, standard/hint/11, standard/hint/12, standard/hint/13, standard/hint/14, standard/hint/15, standard/hint/16, standard/hint/17, standard/hint/18, standard/hint/19, standard/hint/2, standard/hint/20, standard/hint/21, standard/hint/22, standard/hint/23, standard/hint/3, standard/hint/4, standard/hint/5, standard/hint/6, standard/hint/7, standard/hint/8, standard/hint/9 | `{"extension": "92dbf2821472", "family": "d2d5a636506c", "family_version": 2, "instructions": {"s1": "767b584e36e1", "s2": "71bec6b6f25f", "s3": "944e9bef97c2", "s4": "b3fc8b531ff5"}, "policies": "d85d3ec7f7d8", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/twin/0, standard/twin/1, standard/twin/10, standard/twin/11, standard/twin/12, standard/twin/13, standard/twin/14, standard/twin/15, standard/twin/16, standard/twin/17, standard/twin/18, standard/twin/19, standard/twin/2, standard/twin/20, standard/twin/21, standard/twin/22, standard/twin/23, standard/twin/3, standard/twin/4, standard/twin/5, standard/twin/6, standard/twin/7, standard/twin/8, standard/twin/9 | `{"extension": "92dbf2821472", "family": "d2d5a636506c", "family_version": 2, "instructions": {"s1": "0fe425c2d307", "s2": "c7d7d4770b3c", "s3": "cebedf448a37", "s4": "f5558f665a2a"}, "policies": "d85d3ec7f7d8", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/team-record/0, standard/team-record/1, standard/team-record/10, standard/team-record/11, standard/team-record/12, standard/team-record/13, standard/team-record/14, standard/team-record/15, standard/team-record/16, standard/team-record/17, standard/team-record/18, standard/team-record/19, standard/team-record/2, standard/team-record/20, standard/team-record/21, standard/team-record/22, standard/team-record/23, standard/team-record/3, standard/team-record/4, standard/team-record/5, standard/team-record/6, standard/team-record/7, standard/team-record/8, standard/team-record/9 | `{"extension": "92dbf2821472", "family": "d2d5a636506c", "family_version": 2, "instructions": {"s1": "ab4eb85f8e3a", "s2": "53048a5abd8e", "s3": "93feb25cd995", "s4": "fc05deb1c83b"}, "policies": "d85d3ec7f7d8", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/team-fullrecord/0, standard/team-fullrecord/1, standard/team-fullrecord/10, standard/team-fullrecord/11, standard/team-fullrecord/12, standard/team-fullrecord/13, standard/team-fullrecord/14, standard/team-fullrecord/15, standard/team-fullrecord/16, standard/team-fullrecord/17, standard/team-fullrecord/18, standard/team-fullrecord/19, standard/team-fullrecord/2, standard/team-fullrecord/20, standard/team-fullrecord/21, standard/team-fullrecord/22, standard/team-fullrecord/23, standard/team-fullrecord/3, standard/team-fullrecord/4, standard/team-fullrecord/5, standard/team-fullrecord/6, standard/team-fullrecord/7, standard/team-fullrecord/8, standard/team-fullrecord/9 | `{"extension": "92dbf2821472", "family": "d2d5a636506c", "family_version": 2, "instructions": {"s1": "8162e011620b", "s2": "56ed02a0c0cc", "s3": "112350465b0e", "s4": "c1d2fe76913b"}, "policies": "d85d3ec7f7d8", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/probe-hunt-control/0, standard/probe-hunt-control/1, standard/probe-hunt-control/10, standard/probe-hunt-control/11, standard/probe-hunt-control/12, standard/probe-hunt-control/13, standard/probe-hunt-control/14, standard/probe-hunt-control/15, standard/probe-hunt-control/16, standard/probe-hunt-control/17, standard/probe-hunt-control/18, standard/probe-hunt-control/19, standard/probe-hunt-control/2, standard/probe-hunt-control/20, standard/probe-hunt-control/21, standard/probe-hunt-control/22, standard/probe-hunt-control/23, standard/probe-hunt-control/3, standard/probe-hunt-control/4, standard/probe-hunt-control/5, standard/probe-hunt-control/6, standard/probe-hunt-control/7, standard/probe-hunt-control/8, standard/probe-hunt-control/9, standard/probe-hunt-trap/0, standard/probe-hunt-trap/1, standard/probe-hunt-trap/10, standard/probe-hunt-trap/11, standard/probe-hunt-trap/12, standard/probe-hunt-trap/13, standard/probe-hunt-trap/14, standard/probe-hunt-trap/15, standard/probe-hunt-trap/16, standard/probe-hunt-trap/17, standard/probe-hunt-trap/18, standard/probe-hunt-trap/19, standard/probe-hunt-trap/2, standard/probe-hunt-trap/20, standard/probe-hunt-trap/21, standard/probe-hunt-trap/22, standard/probe-hunt-trap/23, standard/probe-hunt-trap/3, standard/probe-hunt-trap/4, standard/probe-hunt-trap/5, standard/probe-hunt-trap/6, standard/probe-hunt-trap/7, standard/probe-hunt-trap/8, standard/probe-hunt-trap/9, standard/probe-ref-control/0, standard/probe-ref-control/1, standard/probe-ref-control/10, standard/probe-ref-control/11, standard/probe-ref-control/12, standard/probe-ref-control/13, standard/probe-ref-control/14, standard/probe-ref-control/15, standard/probe-ref-control/16, standard/probe-ref-control/17, standard/probe-ref-control/18, standard/probe-ref-control/19, standard/probe-ref-control/2, standard/probe-ref-control/20, standard/probe-ref-control/21, standard/probe-ref-control/22, standard/probe-ref-control/23, standard/probe-ref-control/3, standard/probe-ref-control/4, standard/probe-ref-control/5, standard/probe-ref-control/6, standard/probe-ref-control/7, standard/probe-ref-control/8, standard/probe-ref-control/9, standard/probe-ref-trap/0, standard/probe-ref-trap/1, standard/probe-ref-trap/10, standard/probe-ref-trap/11, standard/probe-ref-trap/12, standard/probe-ref-trap/13, standard/probe-ref-trap/14, standard/probe-ref-trap/15, standard/probe-ref-trap/16, standard/probe-ref-trap/17, standard/probe-ref-trap/18, standard/probe-ref-trap/19, standard/probe-ref-trap/2, standard/probe-ref-trap/20, standard/probe-ref-trap/21, standard/probe-ref-trap/22, standard/probe-ref-trap/23, standard/probe-ref-trap/3, standard/probe-ref-trap/4, standard/probe-ref-trap/5, standard/probe-ref-trap/6, standard/probe-ref-trap/7, standard/probe-ref-trap/8, standard/probe-ref-trap/9 | `{"extension": "92dbf2821472", "family": "d2d5a636506c", "family_version": 2, "instructions": {"s1": "905bf5f95b45", "s2": "905bf5f95b45", "s3": "b0cab75e3c12", "s4": "905bf5f95b45"}, "policies": "d85d3ec7f7d8", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo/0, heavy/solo/1, heavy/solo/10, heavy/solo/11, heavy/solo/12, heavy/solo/13, heavy/solo/14, heavy/solo/15, heavy/solo/16, heavy/solo/17, heavy/solo/18, heavy/solo/19, heavy/solo/2, heavy/solo/20, heavy/solo/21, heavy/solo/22, heavy/solo/23, heavy/solo/3, heavy/solo/4, heavy/solo/5, heavy/solo/6, heavy/solo/7, heavy/solo/8, heavy/solo/9 | `{"extension": "92dbf2821472", "family": "d2d5a636506c", "family_version": 2, "instructions": {"s1": "4f285553189a"}, "policies": "d85d3ec7f7d8", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo-seam/0, heavy/solo-seam/1, heavy/solo-seam/10, heavy/solo-seam/11, heavy/solo-seam/12, heavy/solo-seam/13, heavy/solo-seam/14, heavy/solo-seam/15, heavy/solo-seam/16, heavy/solo-seam/17, heavy/solo-seam/18, heavy/solo-seam/19, heavy/solo-seam/2, heavy/solo-seam/20, heavy/solo-seam/21, heavy/solo-seam/22, heavy/solo-seam/23, heavy/solo-seam/3, heavy/solo-seam/4, heavy/solo-seam/5, heavy/solo-seam/6, heavy/solo-seam/7, heavy/solo-seam/8, heavy/solo-seam/9 | `{"extension": "92dbf2821472", "family": "d2d5a636506c", "family_version": 2, "instructions": {"s1": "848c201dfca3", "s2": "5c42b2e8a260", "s3": "7ff59073253c", "s4": "bacba78fe9be", "s5": "9b9bac068eb7", "s6": "8e0aca6e5df0", "s7": "0bd60f4bf46a", "s8": "2478001777b8"}, "policies": "d85d3ec7f7d8", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/team/0, heavy/team/1, heavy/team/10, heavy/team/11, heavy/team/12, heavy/team/13, heavy/team/14, heavy/team/15, heavy/team/16, heavy/team/17, heavy/team/18, heavy/team/19, heavy/team/2, heavy/team/20, heavy/team/21, heavy/team/22, heavy/team/23, heavy/team/3, heavy/team/4, heavy/team/5, heavy/team/6, heavy/team/7, heavy/team/8, heavy/team/9 | `{"extension": "92dbf2821472", "family": "d2d5a636506c", "family_version": 2, "instructions": {"s1": "e57b031436fd", "s2": "fa4892a2b29c", "s3": "9c182df2e854", "s4": "dbc7adef6536", "s5": "68562ca92201", "s6": "545a52953baf", "s7": "8ee49660fd1d", "s8": "2da83c8c2829"}, "policies": "d85d3ec7f7d8", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/hint/0, heavy/hint/1, heavy/hint/10, heavy/hint/11, heavy/hint/12, heavy/hint/13, heavy/hint/14, heavy/hint/15, heavy/hint/16, heavy/hint/17, heavy/hint/18, heavy/hint/19, heavy/hint/2, heavy/hint/20, heavy/hint/21, heavy/hint/22, heavy/hint/23, heavy/hint/3, heavy/hint/4, heavy/hint/5, heavy/hint/6, heavy/hint/7, heavy/hint/8, heavy/hint/9 | `{"extension": "92dbf2821472", "family": "d2d5a636506c", "family_version": 2, "instructions": {"s1": "9495aef46fd8", "s2": "48b915455485", "s3": "66699f865fe5", "s4": "0f257762d022", "s5": "ae1472bc4247", "s6": "42127cae271d", "s7": "7fa644fc5701", "s8": "92659fd0c782"}, "policies": "d85d3ec7f7d8", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/twin/0, heavy/twin/1, heavy/twin/10, heavy/twin/11, heavy/twin/12, heavy/twin/13, heavy/twin/14, heavy/twin/15, heavy/twin/16, heavy/twin/17, heavy/twin/18, heavy/twin/19, heavy/twin/2, heavy/twin/20, heavy/twin/21, heavy/twin/22, heavy/twin/23, heavy/twin/3, heavy/twin/4, heavy/twin/5, heavy/twin/6, heavy/twin/7, heavy/twin/8, heavy/twin/9 | `{"extension": "92dbf2821472", "family": "d2d5a636506c", "family_version": 2, "instructions": {"s1": "2175e3f29fca", "s2": "4d4008d2d7a3", "s3": "81c47bc32e04", "s4": "6e714586c7fa", "s5": "9be94b88d2b4", "s6": "ac1ea90a8ae0", "s7": "fa19facf3c45", "s8": "2989932beb83"}, "policies": "d85d3ec7f7d8", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/team-record/0, heavy/team-record/1, heavy/team-record/10, heavy/team-record/11, heavy/team-record/12, heavy/team-record/13, heavy/team-record/14, heavy/team-record/15, heavy/team-record/16, heavy/team-record/17, heavy/team-record/18, heavy/team-record/19, heavy/team-record/2, heavy/team-record/20, heavy/team-record/21, heavy/team-record/22, heavy/team-record/23, heavy/team-record/3, heavy/team-record/4, heavy/team-record/5, heavy/team-record/6, heavy/team-record/7, heavy/team-record/8, heavy/team-record/9 | `{"extension": "92dbf2821472", "family": "d2d5a636506c", "family_version": 2, "instructions": {"s1": "d18fd645d76b", "s2": "ddd7ebf69933", "s3": "f4c72cb948cf", "s4": "bcdc8ac66229", "s5": "c6e1b65554a2", "s6": "6330d2a2311d", "s7": "8590890fd43a", "s8": "874e5817008e"}, "policies": "d85d3ec7f7d8", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/team-fullrecord/0, heavy/team-fullrecord/1, heavy/team-fullrecord/10, heavy/team-fullrecord/11, heavy/team-fullrecord/12, heavy/team-fullrecord/13, heavy/team-fullrecord/14, heavy/team-fullrecord/15, heavy/team-fullrecord/16, heavy/team-fullrecord/17, heavy/team-fullrecord/18, heavy/team-fullrecord/19, heavy/team-fullrecord/2, heavy/team-fullrecord/20, heavy/team-fullrecord/21, heavy/team-fullrecord/22, heavy/team-fullrecord/23, heavy/team-fullrecord/3, heavy/team-fullrecord/4, heavy/team-fullrecord/5, heavy/team-fullrecord/6, heavy/team-fullrecord/7, heavy/team-fullrecord/8, heavy/team-fullrecord/9 | `{"extension": "92dbf2821472", "family": "d2d5a636506c", "family_version": 2, "instructions": {"s1": "82a2a949ee93", "s2": "c0c55a34fe2d", "s3": "f5ff24e9a94b", "s4": "4e4720aebfa9", "s5": "2108f9e656d6", "s6": "015451e13088", "s7": "9a951d90f58d", "s8": "c2423b910007"}, "policies": "d85d3ec7f7d8", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/probe-hunt-control/0, heavy/probe-hunt-control/1, heavy/probe-hunt-control/10, heavy/probe-hunt-control/11, heavy/probe-hunt-control/12, heavy/probe-hunt-control/13, heavy/probe-hunt-control/14, heavy/probe-hunt-control/15, heavy/probe-hunt-control/16, heavy/probe-hunt-control/17, heavy/probe-hunt-control/18, heavy/probe-hunt-control/19, heavy/probe-hunt-control/2, heavy/probe-hunt-control/20, heavy/probe-hunt-control/21, heavy/probe-hunt-control/22, heavy/probe-hunt-control/23, heavy/probe-hunt-control/3, heavy/probe-hunt-control/4, heavy/probe-hunt-control/5, heavy/probe-hunt-control/6, heavy/probe-hunt-control/7, heavy/probe-hunt-control/8, heavy/probe-hunt-control/9, heavy/probe-hunt-trap/0, heavy/probe-hunt-trap/1, heavy/probe-hunt-trap/10, heavy/probe-hunt-trap/11, heavy/probe-hunt-trap/12, heavy/probe-hunt-trap/13, heavy/probe-hunt-trap/14, heavy/probe-hunt-trap/15, heavy/probe-hunt-trap/16, heavy/probe-hunt-trap/17, heavy/probe-hunt-trap/18, heavy/probe-hunt-trap/19, heavy/probe-hunt-trap/2, heavy/probe-hunt-trap/20, heavy/probe-hunt-trap/21, heavy/probe-hunt-trap/22, heavy/probe-hunt-trap/23, heavy/probe-hunt-trap/3, heavy/probe-hunt-trap/4, heavy/probe-hunt-trap/5, heavy/probe-hunt-trap/6, heavy/probe-hunt-trap/7, heavy/probe-hunt-trap/8, heavy/probe-hunt-trap/9, heavy/probe-ref-control/0, heavy/probe-ref-control/1, heavy/probe-ref-control/10, heavy/probe-ref-control/11, heavy/probe-ref-control/12, heavy/probe-ref-control/13, heavy/probe-ref-control/14, heavy/probe-ref-control/15, heavy/probe-ref-control/16, heavy/probe-ref-control/17, heavy/probe-ref-control/18, heavy/probe-ref-control/19, heavy/probe-ref-control/2, heavy/probe-ref-control/20, heavy/probe-ref-control/21, heavy/probe-ref-control/22, heavy/probe-ref-control/23, heavy/probe-ref-control/3, heavy/probe-ref-control/4, heavy/probe-ref-control/5, heavy/probe-ref-control/6, heavy/probe-ref-control/7, heavy/probe-ref-control/8, heavy/probe-ref-control/9, heavy/probe-ref-trap/0, heavy/probe-ref-trap/1, heavy/probe-ref-trap/10, heavy/probe-ref-trap/11, heavy/probe-ref-trap/12, heavy/probe-ref-trap/13, heavy/probe-ref-trap/14, heavy/probe-ref-trap/15, heavy/probe-ref-trap/16, heavy/probe-ref-trap/17, heavy/probe-ref-trap/18, heavy/probe-ref-trap/19, heavy/probe-ref-trap/2, heavy/probe-ref-trap/20, heavy/probe-ref-trap/21, heavy/probe-ref-trap/22, heavy/probe-ref-trap/23, heavy/probe-ref-trap/3, heavy/probe-ref-trap/4, heavy/probe-ref-trap/5, heavy/probe-ref-trap/6, heavy/probe-ref-trap/7, heavy/probe-ref-trap/8, heavy/probe-ref-trap/9 | `{"extension": "92dbf2821472", "family": "d2d5a636506c", "family_version": 2, "instructions": {"s1": "905bf5f95b45", "s2": "905bf5f95b45", "s3": "905bf5f95b45", "s4": "905bf5f95b45", "s5": "905bf5f95b45", "s6": "905bf5f95b45", "s7": "8ee49660fd1d", "s8": "905bf5f95b45"}, "policies": "d85d3ec7f7d8", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |

## Load and cost (estimates)

Estimate: 4 characters per input token; repeat the instruction, JSON briefing and seat files once per simulated world request (at least once per model seat). Excludes scripted seats, system prompts, replies, history and output tokens. Requests are not measured model turns.

| Level / arm | Per-seat max calls / briefing chars | Mean estimated tokens | $1 / $5 / $15 per M |
|---|---|---|---|
| canary / solo | s1: 33/57 | 30525 | $0.03053 / $0.15263 / $0.45787 |
| canary / solo-seam | s1: 12/58, s2: 12/58, s3: 13/58 | 35483 | $0.03548 / $0.17741 / $0.53224 |
| canary / team | s1: 12/58, s2: 12/58, s3: 13/58 | 35039 | $0.03504 / $0.17519 / $0.52558 |
| canary / hint | s1: 12/58, s2: 12/58, s3: 13/58 | 36075 | $0.03608 / $0.18038 / $0.54112 |
| canary / twin | s1: 12/58, s2: 12/58, s3: 13/58 | 35927 | $0.03593 / $0.17963 / $0.53890 |
| canary / team-record | s1: 12/58, s2: 12/58, s3: 13/58 | 35816 | $0.03582 / $0.17908 / $0.53724 |
| canary / team-fullrecord | s1: 12/58, s2: 12/58, s3: 13/58 | 36038 | $0.03604 / $0.18019 / $0.54057 |
| canary / probe-hunt-control | s1: 12/58, s2: 12/58, s3: 13/58 | 11364 | $0.01136 / $0.05682 / $0.17046 |
| canary / probe-hunt-trap | s1: 12/58, s2: 12/58, s3: 13/58 | 11364 | $0.01136 / $0.05682 / $0.17046 |
| canary / probe-ref-control | s1: 12/58, s2: 12/58, s3: 13/58 | 11364 | $0.01136 / $0.05682 / $0.17046 |
| canary / probe-ref-trap | s1: 12/58, s2: 12/58, s3: 13/58 | 11364 | $0.01136 / $0.05682 / $0.17046 |
| standard / solo | s1: 43/57 | 39775 | $0.03977 / $0.19887 / $0.59662 |
| standard / solo-seam | s1: 12/58, s2: 12/58, s3: 12/58, s4: 13/58 | 46991 | $0.04699 / $0.23495 / $0.70486 |
| standard / team | s1: 12/58, s2: 12/58, s3: 12/58, s4: 13/58 | 46403 | $0.04640 / $0.23201 / $0.69605 |
| standard / hint | s1: 12/58, s2: 12/58, s3: 12/58, s4: 13/58 | 47775 | $0.04777 / $0.23888 / $0.71662 |
| standard / twin | s1: 12/58, s2: 12/58, s3: 12/58, s4: 13/58 | 47579 | $0.04758 / $0.23789 / $0.71369 |
| standard / team-record | s1: 12/58, s2: 12/58, s3: 12/58, s4: 13/58 | 47432 | $0.04743 / $0.23716 / $0.71148 |
| standard / team-fullrecord | s1: 12/58, s2: 12/58, s3: 12/58, s4: 13/58 | 47726 | $0.04773 / $0.23863 / $0.71589 |
| standard / probe-hunt-control | s1: 12/58, s2: 12/58, s3: 12/58, s4: 13/58 | 11364 | $0.01136 / $0.05682 / $0.17046 |
| standard / probe-hunt-trap | s1: 12/58, s2: 12/58, s3: 12/58, s4: 13/58 | 11364 | $0.01136 / $0.05682 / $0.17046 |
| standard / probe-ref-control | s1: 12/58, s2: 12/58, s3: 12/58, s4: 13/58 | 11364 | $0.01136 / $0.05682 / $0.17046 |
| standard / probe-ref-trap | s1: 12/58, s2: 12/58, s3: 12/58, s4: 13/58 | 11364 | $0.01136 / $0.05682 / $0.17046 |
| heavy / solo | s1: 83/57 | 76775 | $0.07677 / $0.38388 / $1.15162 |
| heavy / solo-seam | s1: 12/58, s2: 12/58, s3: 12/58, s4: 12/58, s5: 12/58, s6: 12/58, s7: 12/58, s8: 13/58 | 93023 | $0.09302 / $0.46512 / $1.39535 |
| heavy / team | s1: 12/58, s2: 12/58, s3: 12/58, s4: 12/58, s5: 12/58, s6: 12/58, s7: 12/58, s8: 13/58 | 91859 | $0.09186 / $0.45930 / $1.37789 |
| heavy / hint | s1: 12/58, s2: 12/58, s3: 12/58, s4: 12/58, s5: 12/58, s6: 12/58, s7: 12/58, s8: 13/58 | 94575 | $0.09458 / $0.47287 / $1.41863 |
| heavy / twin | s1: 12/58, s2: 12/58, s3: 12/58, s4: 12/58, s5: 12/58, s6: 12/58, s7: 12/58, s8: 13/58 | 94187 | $0.09419 / $0.47093 / $1.41281 |
| heavy / team-record | s1: 12/58, s2: 12/58, s3: 12/58, s4: 12/58, s5: 12/58, s6: 12/58, s7: 12/58, s8: 13/58 | 93896 | $0.09390 / $0.46948 / $1.40844 |
| heavy / team-fullrecord | s1: 12/58, s2: 12/58, s3: 12/58, s4: 12/58, s5: 12/58, s6: 12/58, s7: 12/58, s8: 13/58 | 94478 | $0.09448 / $0.47239 / $1.41717 |
| heavy / probe-hunt-control | s1: 12/58, s2: 12/58, s3: 12/58, s4: 12/58, s5: 12/58, s6: 12/58, s7: 12/58, s8: 13/58 | 11364 | $0.01136 / $0.05682 / $0.17046 |
| heavy / probe-hunt-trap | s1: 12/58, s2: 12/58, s3: 12/58, s4: 12/58, s5: 12/58, s6: 12/58, s7: 12/58, s8: 13/58 | 11364 | $0.01136 / $0.05682 / $0.17046 |
| heavy / probe-ref-control | s1: 12/58, s2: 12/58, s3: 12/58, s4: 12/58, s5: 12/58, s6: 12/58, s7: 12/58, s8: 13/58 | 11364 | $0.01136 / $0.05682 / $0.17046 |
| heavy / probe-ref-trap | s1: 12/58, s2: 12/58, s3: 12/58, s4: 12/58, s5: 12/58, s6: 12/58, s7: 12/58, s8: 13/58 | 11364 | $0.01136 / $0.05682 / $0.17046 |

## Evidence and predictions

[flotilla/docs/seams/handoff.md](file:///Users/petermakhnatch/Developer/flotilla-wt/seams-handoff/flotilla/docs/seams/handoff.md)

## Open risks

- NA: floor

Scripted policy results are not estimates of frontier-model performance.
