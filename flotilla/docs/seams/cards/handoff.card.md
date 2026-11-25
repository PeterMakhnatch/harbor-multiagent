# EvalCard: handoff — Operate a print dispatch desk

**Seam:** A capped note carries unfinished work and unresolved evidence to a fresh context across shifts.
**Kit commit:** `f41ff76060159a0a3e5fb6916a7cb4ff9676a2cc`; variant: `default`.
**Arms:** solo, solo-seam, team, hint, twin, team-record, team-fullrecord, probe-hunt-control, probe-hunt-trap, probe-ref-control, probe-ref-trap. Levels: canary (canary), standard (standard), heavy (heavy).
**Sweep:** 24 instances × 3 schedule seeds. No model calls.
**Policies unavailable:** random, script-loop.

## Policy matrix (mean / min / n; invalid count in parentheses)

| Level | Arm | Seats | careful | naive | nop | blind | lone | lapse-10 | lapse-20 | lapse-30 | lapse-50 | pointer |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| canary | solo | s1 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| canary | solo-seam | s1, s2, s3 | 1.000 / 1.000 / 72 (0) | 0.500 / 0.500 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.333 / 0.333 / 72 (0) | 0.333 / 0.333 / 72 (0) | 0.818 / 0.417 / 72 (0) | 0.635 / 0.417 / 72 (0) | 0.552 / 0.375 / 72 (0) | 0.439 / 0.333 / 72 (0) | 0.500 / 0.500 / 72 (0) |
| canary | team | s1, s2, s3 | 1.000 / 1.000 / 72 (0) | 0.500 / 0.500 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.333 / 0.333 / 72 (0) | 0.333 / 0.333 / 72 (0) | 0.818 / 0.417 / 72 (0) | 0.635 / 0.417 / 72 (0) | 0.552 / 0.375 / 72 (0) | 0.439 / 0.333 / 72 (0) | 0.500 / 0.500 / 72 (0) |
| canary | hint | s1, s2, s3 | 1.000 / 1.000 / 72 (0) | 0.500 / 0.500 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.333 / 0.333 / 72 (0) | 0.333 / 0.333 / 72 (0) | 0.818 / 0.417 / 72 (0) | 0.635 / 0.417 / 72 (0) | 0.552 / 0.375 / 72 (0) | 0.439 / 0.333 / 72 (0) | 0.500 / 0.500 / 72 (0) |
| canary | twin | s1, s2, s3 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.333 / 0.333 / 72 (0) | 0.333 / 0.333 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| canary | team-record | s1, s2, s3 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.333 / 0.333 / 72 (0) | 0.333 / 0.333 / 72 (0) | 0.818 / 0.417 / 72 (0) | 0.635 / 0.417 / 72 (0) | 0.552 / 0.375 / 72 (0) | 0.439 / 0.333 / 72 (0) | 0.500 / 0.500 / 72 (0) |
| canary | team-fullrecord | s1, s2, s3 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.333 / 0.333 / 72 (0) | 0.333 / 0.333 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| canary | probe-hunt-control | s1, s2, s3 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| canary | probe-hunt-trap | s1, s2, s3 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.917 / 0.000 / 72 (0) | 0.792 / 0.000 / 72 (0) | 0.667 / 0.000 / 72 (0) | 0.417 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) |
| canary | probe-ref-control | s1, s2, s3 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| canary | probe-ref-trap | s1, s2, s3 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.792 / 0.000 / 72 (0) | 0.917 / 0.000 / 72 (0) | 0.625 / 0.000 / 72 (0) | 0.500 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard | solo | s1 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard | solo-seam | s1, s2, s3, s4 | 1.000 / 1.000 / 72 (0) | 0.375 / 0.375 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.312 / 0.312 / 72 (0) | 0.312 / 0.312 / 72 (0) | 0.805 / 0.438 / 72 (0) | 0.714 / 0.406 / 72 (0) | 0.520 / 0.344 / 72 (0) | 0.410 / 0.344 / 72 (0) | 0.500 / 0.500 / 72 (0) |
| standard | team | s1, s2, s3, s4 | 1.000 / 1.000 / 72 (0) | 0.375 / 0.375 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.312 / 0.312 / 72 (0) | 0.312 / 0.312 / 72 (0) | 0.805 / 0.438 / 72 (0) | 0.714 / 0.406 / 72 (0) | 0.520 / 0.344 / 72 (0) | 0.410 / 0.344 / 72 (0) | 0.500 / 0.500 / 72 (0) |
| standard | hint | s1, s2, s3, s4 | 1.000 / 1.000 / 72 (0) | 0.375 / 0.375 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.312 / 0.312 / 72 (0) | 0.312 / 0.312 / 72 (0) | 0.805 / 0.438 / 72 (0) | 0.714 / 0.406 / 72 (0) | 0.520 / 0.344 / 72 (0) | 0.410 / 0.344 / 72 (0) | 0.500 / 0.500 / 72 (0) |
| standard | twin | s1, s2, s3, s4 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.312 / 0.312 / 72 (0) | 0.312 / 0.312 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard | team-record | s1, s2, s3, s4 | 1.000 / 1.000 / 72 (0) | 0.771 / 0.375 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.312 / 0.312 / 72 (0) | 0.312 / 0.312 / 72 (0) | 0.805 / 0.438 / 72 (0) | 0.714 / 0.406 / 72 (0) | 0.520 / 0.344 / 72 (0) | 0.410 / 0.344 / 72 (0) | 0.500 / 0.500 / 72 (0) |
| standard | team-fullrecord | s1, s2, s3, s4 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.312 / 0.312 / 72 (0) | 0.312 / 0.312 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard | probe-hunt-control | s1, s2, s3, s4 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard | probe-hunt-trap | s1, s2, s3, s4 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.958 / 0.000 / 72 (0) | 0.792 / 0.000 / 72 (0) | 0.583 / 0.000 / 72 (0) | 0.208 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) |
| standard | probe-ref-control | s1, s2, s3, s4 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard | probe-ref-trap | s1, s2, s3, s4 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.792 / 0.000 / 72 (0) | 0.542 / 0.000 / 72 (0) | 0.333 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy | solo | s1 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy | solo-seam | s1, s2, s3, s4, s5, s6, s7, s8 | 1.000 / 1.000 / 72 (0) | 0.312 / 0.312 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.281 / 0.281 / 72 (0) | 0.281 / 0.281 / 72 (0) | 0.760 / 0.422 / 72 (0) | 0.541 / 0.312 / 72 (0) | 0.373 / 0.312 / 72 (0) | 0.339 / 0.281 / 72 (0) | 0.500 / 0.500 / 72 (0) |
| heavy | team | s1, s2, s3, s4, s5, s6, s7, s8 | 1.000 / 1.000 / 72 (0) | 0.312 / 0.312 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.281 / 0.281 / 72 (0) | 0.281 / 0.281 / 72 (0) | 0.760 / 0.422 / 72 (0) | 0.541 / 0.312 / 72 (0) | 0.373 / 0.312 / 72 (0) | 0.339 / 0.281 / 72 (0) | 0.500 / 0.500 / 72 (0) |
| heavy | hint | s1, s2, s3, s4, s5, s6, s7, s8 | 1.000 / 1.000 / 72 (0) | 0.312 / 0.312 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.281 / 0.281 / 72 (0) | 0.281 / 0.281 / 72 (0) | 0.760 / 0.422 / 72 (0) | 0.541 / 0.312 / 72 (0) | 0.373 / 0.312 / 72 (0) | 0.339 / 0.281 / 72 (0) | 0.500 / 0.500 / 72 (0) |
| heavy | twin | s1, s2, s3, s4, s5, s6, s7, s8 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.281 / 0.281 / 72 (0) | 0.281 / 0.281 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy | team-record | s1, s2, s3, s4, s5, s6, s7, s8 | 1.000 / 1.000 / 72 (0) | 0.812 / 0.812 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.281 / 0.281 / 72 (0) | 0.281 / 0.281 / 72 (0) | 0.760 / 0.422 / 72 (0) | 0.541 / 0.312 / 72 (0) | 0.373 / 0.312 / 72 (0) | 0.339 / 0.281 / 72 (0) | 0.500 / 0.500 / 72 (0) |
| heavy | team-fullrecord | s1, s2, s3, s4, s5, s6, s7, s8 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.281 / 0.281 / 72 (0) | 0.281 / 0.281 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy | probe-hunt-control | s1, s2, s3, s4, s5, s6, s7, s8 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy | probe-hunt-trap | s1, s2, s3, s4, s5, s6, s7, s8 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.792 / 0.000 / 72 (0) | 0.458 / 0.000 / 72 (0) | 0.208 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) |
| heavy | probe-ref-control | s1, s2, s3, s4, s5, s6, s7, s8 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy | probe-ref-trap | s1, s2, s3, s4, s5, s6, s7, s8 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.875 / 0.000 / 72 (0) | 0.792 / 0.000 / 72 (0) | 0.542 / 0.000 / 72 (0) | 0.417 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |

## Checks

| Check | Standard | Result | Evidence |
|---|---|---|---|
| levels | A5 | **PASS** | All requested dose levels available |
| solvable | A2/B1 | **PASS** | 2376 runs; careful reward = 1 |
| easy alone | A2 | **PASS** | 216 runs; solo careful reward = 1 |
| floor | A3 | **PASS** | 2376 runs; every nop reward ≤ 0.05 |
| chance | A3 | **PASS** | canary/team/blind: actual 0.3333333333333333, expected 0.3333 (absolute tolerance 0.1); canary/probe-hunt-trap/blind: actual 0.0, expected 0.0000 (absolute tolerance 0.1); canary/probe-ref-trap/blind: actual 0.0, expected 0.0000 (absolute tolerance 0.1); standard/team/blind: actual 0.3125, expected 0.3125 (absolute tolerance 0.1); standard/probe-hunt-trap/blind: actual 0.0, expected 0.0000 (abs… (full evidence in JSON) |
| separation | A4 | **PASS** | canary/probe-hunt-trap: gap 1.000; canary/probe-ref-trap: gap 1.000; canary/probe-hunt-control: naive min 1.0; canary/probe-ref-control: naive min 1.0; standard/probe-hunt-trap: gap 1.000; standard/probe-ref-trap: gap 1.000; standard/probe-hunt-control: naive min 1.0; standard/probe-ref-control: naive min 1.0; heavy/probe-hunt-trap: gap 1.000; heavy/probe-ref-trap: gap 1.000; heavy/probe-hunt-c… (full evidence in JSON) |
| dose response | A5/I1 | **PASS** | canary → standard → heavy: [0.5, 0.375, 0.3125] |
| one seat is not enough | A3/C1 | **PASS** | 216 runs; every lone team reward ≤ 0.5 |
| schedule independence | E3 | **PASS** | 2376 runs; careful scores 1 across 3 schedule seed(s); max within-instance naive spread: 0.0 |
| simulation validity | F1 | **PASS** | 23760 runs; No invalid runs in the ordinary sweep |
| determinism | E1/E2 | **PASS** | 66/66 sample event hashes match |
| no leak | C1/D2 | **PASS** | 792 built tasks; seat views and family QA scanned |
| pairs | A1/D2 | **PASS** | probe-hunt-control→probe-hunt-trap: 72 pairs, max 0 changed chars; fields: none; probe-ref-control→probe-ref-trap: 72 pairs, max 0 changed chars; fields: none; team→hint: 72 pairs, max 896 changed chars; fields: s1/instruction, s2/instruction, s3/instruction, s4/instruction, s5/instruction, s6/instruction, s7/instruction, s8/instruction; team→solo-seam: 72 pairs, max 976 changed chars; fields: … (full evidence in JSON) |
| twin rule | A1 | **PASS** | Every team/trap has a declared twin |
| invalid is not zero | F1 | **PASS** | Budget-limited simulation: stalled=True, invalid=True, reward=None, stalled_seats=['s1'] (a cut-off play is void or a valid result that names the stalled seats, never an unflagged score) |
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
| canary | nop | boundary | team-fullrecord − team | +0.000 | 72 / 24 | 0 |
| canary | nop | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | nop | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | blind | boundary | team-fullrecord − team | +0.000 | 72 / 24 | 0 |
| canary | blind | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | blind | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | lone | boundary | team-fullrecord − team | +0.000 | 72 / 24 | 0 |
| canary | lone | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | lone | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-10 | boundary | team-fullrecord − team | +0.182 | 72 / 24 | 0 |
| canary | lapse-10 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-10 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-20 | boundary | team-fullrecord − team | +0.365 | 72 / 24 | 0 |
| canary | lapse-20 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-20 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-30 | boundary | team-fullrecord − team | +0.448 | 72 / 24 | 0 |
| canary | lapse-30 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-30 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-50 | boundary | team-fullrecord − team | +0.561 | 72 / 24 | 0 |
| canary | lapse-50 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-50 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | pointer | boundary | team-fullrecord − team | +0.500 | 72 / 24 | 0 |
| canary | pointer | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | pointer | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | careful | boundary | team-fullrecord − team | +0.000 | 72 / 24 | 0 |
| standard | careful | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | careful | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | naive | boundary | team-fullrecord − team | +0.625 | 72 / 24 | 0 |
| standard | naive | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | naive | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | nop | boundary | team-fullrecord − team | +0.000 | 72 / 24 | 0 |
| standard | nop | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | nop | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | blind | boundary | team-fullrecord − team | +0.000 | 72 / 24 | 0 |
| standard | blind | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | blind | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | lone | boundary | team-fullrecord − team | +0.000 | 72 / 24 | 0 |
| standard | lone | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | lone | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-10 | boundary | team-fullrecord − team | +0.195 | 72 / 24 | 0 |
| standard | lapse-10 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-10 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-20 | boundary | team-fullrecord − team | +0.286 | 72 / 24 | 0 |
| standard | lapse-20 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-20 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-30 | boundary | team-fullrecord − team | +0.480 | 72 / 24 | 0 |
| standard | lapse-30 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-30 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-50 | boundary | team-fullrecord − team | +0.590 | 72 / 24 | 0 |
| standard | lapse-50 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-50 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | pointer | boundary | team-fullrecord − team | +0.500 | 72 / 24 | 0 |
| standard | pointer | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | pointer | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | careful | boundary | team-fullrecord − team | +0.000 | 72 / 24 | 0 |
| heavy | careful | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | careful | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | naive | boundary | team-fullrecord − team | +0.688 | 72 / 24 | 0 |
| heavy | naive | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | naive | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | nop | boundary | team-fullrecord − team | +0.000 | 72 / 24 | 0 |
| heavy | nop | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | nop | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | blind | boundary | team-fullrecord − team | +0.000 | 72 / 24 | 0 |
| heavy | blind | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | blind | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | lone | boundary | team-fullrecord − team | +0.000 | 72 / 24 | 0 |
| heavy | lone | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | lone | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | lapse-10 | boundary | team-fullrecord − team | +0.240 | 72 / 24 | 0 |
| heavy | lapse-10 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | lapse-10 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | lapse-20 | boundary | team-fullrecord − team | +0.459 | 72 / 24 | 0 |
| heavy | lapse-20 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | lapse-20 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | lapse-30 | boundary | team-fullrecord − team | +0.627 | 72 / 24 | 0 |
| heavy | lapse-30 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | lapse-30 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | lapse-50 | boundary | team-fullrecord − team | +0.661 | 72 / 24 | 0 |
| heavy | lapse-50 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | lapse-50 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | pointer | boundary | team-fullrecord − team | +0.500 | 72 / 24 | 0 |
| heavy | pointer | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | pointer | protocol | hint − team | +0.000 | 72 / 24 | 0 |

## Power plan (scripted stand-ins)

Fixed sample, exact two-sided McNemar, α=0.05, target power 0.8; binary success means reward = 1. The paired reward effect also includes partial credit.
Schedule repeats estimate outcome probabilities; they are not independent instances. The planned n assumes independent new instances, one paired run each.
No-effect and NA cells have no planned n. Plans search up to 1,000 instances. These are sensitivity estimates, not model performance predictions.

| Level | Policy | Control − treatment | Reward effect | Success effect | + / − | Valid pairs / instances | Excluded | Planned n |
|---|---|---|---|---|---|---|---|---|
| canary | lapse-10 | probe-hunt-control − probe-hunt-trap | +0.083 | +0.083 | 6 / 0 | 72 / 24 | 0 | 94 |
| canary | lapse-10 | probe-ref-control − probe-ref-trap | +0.208 | +0.208 | 15 / 0 | 72 / 24 | 0 | 37 |
| canary | lapse-10 | team-fullrecord − team | +0.182 | +0.417 | 30 / 0 | 72 / 24 | 0 | 18 |
| canary | lapse-20 | probe-hunt-control − probe-hunt-trap | +0.208 | +0.208 | 15 / 0 | 72 / 24 | 0 | 37 |
| canary | lapse-20 | probe-ref-control − probe-ref-trap | +0.083 | +0.083 | 6 / 0 | 72 / 24 | 0 | 94 |
| canary | lapse-20 | team-fullrecord − team | +0.365 | +0.833 | 60 / 0 | 72 / 24 | 0 | 8 |
| canary | lapse-30 | probe-hunt-control − probe-hunt-trap | +0.333 | +0.333 | 24 / 0 | 72 / 24 | 0 | 23 |
| canary | lapse-30 | probe-ref-control − probe-ref-trap | +0.375 | +0.375 | 27 / 0 | 72 / 24 | 0 | 20 |
| canary | lapse-30 | team-fullrecord − team | +0.448 | +0.917 | 66 / 0 | 72 / 24 | 0 | 7 |
| canary | lapse-50 | probe-hunt-control − probe-hunt-trap | +0.583 | +0.583 | 42 / 0 | 72 / 24 | 0 | 12 |
| canary | lapse-50 | probe-ref-control − probe-ref-trap | +0.500 | +0.500 | 36 / 0 | 72 / 24 | 0 | 15 |
| canary | lapse-50 | team-fullrecord − team | +0.561 | +1.000 | 72 / 0 | 72 / 24 | 0 | 6 |
| standard | lapse-10 | probe-hunt-control − probe-hunt-trap | +0.042 | +0.042 | 3 / 0 | 72 / 24 | 0 | 189 |
| standard | lapse-10 | probe-ref-control − probe-ref-trap | +0.000 | +0.000 | 0 / 0 | 72 / 24 | 0 | no effect |
| standard | lapse-10 | team-fullrecord − team | +0.195 | +0.750 | 54 / 0 | 72 / 24 | 0 | 9 |
| standard | lapse-20 | probe-hunt-control − probe-hunt-trap | +0.208 | +0.208 | 15 / 0 | 72 / 24 | 0 | 37 |
| standard | lapse-20 | probe-ref-control − probe-ref-trap | +0.208 | +0.208 | 15 / 0 | 72 / 24 | 0 | 37 |
| standard | lapse-20 | team-fullrecord − team | +0.286 | +0.917 | 66 / 0 | 72 / 24 | 0 | 7 |
| standard | lapse-30 | probe-hunt-control − probe-hunt-trap | +0.417 | +0.417 | 30 / 0 | 72 / 24 | 0 | 18 |
| standard | lapse-30 | probe-ref-control − probe-ref-trap | +0.458 | +0.458 | 33 / 0 | 72 / 24 | 0 | 16 |
| standard | lapse-30 | team-fullrecord − team | +0.480 | +1.000 | 72 / 0 | 72 / 24 | 0 | 6 |
| standard | lapse-50 | probe-hunt-control − probe-hunt-trap | +0.792 | +0.792 | 57 / 0 | 72 / 24 | 0 | 9 |
| standard | lapse-50 | probe-ref-control − probe-ref-trap | +0.667 | +0.667 | 48 / 0 | 72 / 24 | 0 | 11 |
| standard | lapse-50 | team-fullrecord − team | +0.590 | +1.000 | 72 / 0 | 72 / 24 | 0 | 6 |
| heavy | lapse-10 | probe-hunt-control − probe-hunt-trap | +0.000 | +0.000 | 0 / 0 | 72 / 24 | 0 | no effect |
| heavy | lapse-10 | probe-ref-control − probe-ref-trap | +0.125 | +0.125 | 9 / 0 | 72 / 24 | 0 | 62 |
| heavy | lapse-10 | team-fullrecord − team | +0.240 | +1.000 | 72 / 0 | 72 / 24 | 0 | 6 |
| heavy | lapse-20 | probe-hunt-control − probe-hunt-trap | +0.208 | +0.208 | 15 / 0 | 72 / 24 | 0 | 37 |
| heavy | lapse-20 | probe-ref-control − probe-ref-trap | +0.208 | +0.208 | 15 / 0 | 72 / 24 | 0 | 37 |
| heavy | lapse-20 | team-fullrecord − team | +0.459 | +1.000 | 72 / 0 | 72 / 24 | 0 | 6 |
| heavy | lapse-30 | probe-hunt-control − probe-hunt-trap | +0.542 | +0.542 | 39 / 0 | 72 / 24 | 0 | 13 |
| heavy | lapse-30 | probe-ref-control − probe-ref-trap | +0.458 | +0.458 | 33 / 0 | 72 / 24 | 0 | 16 |
| heavy | lapse-30 | team-fullrecord − team | +0.627 | +1.000 | 72 / 0 | 72 / 24 | 0 | 6 |
| heavy | lapse-50 | probe-hunt-control − probe-hunt-trap | +0.792 | +0.792 | 57 / 0 | 72 / 24 | 0 | 9 |
| heavy | lapse-50 | probe-ref-control − probe-ref-trap | +0.583 | +0.583 | 42 / 0 | 72 / 24 | 0 | 12 |
| heavy | lapse-50 | team-fullrecord − team | +0.661 | +1.000 | 72 / 0 | 72 / 24 | 0 | 6 |

## Versions

| Task(s) | Simulator versions |
|---|---|
| canary/solo/0, canary/solo/1, canary/solo/10, canary/solo/11, canary/solo/12, canary/solo/13, canary/solo/14, canary/solo/15, canary/solo/16, canary/solo/17, canary/solo/18, canary/solo/19, canary/solo/2, canary/solo/20, canary/solo/21, canary/solo/22, canary/solo/23, canary/solo/3, canary/solo/4, canary/solo/5, canary/solo/6, canary/solo/7, canary/solo/8, canary/solo/9 | `{"extension": "cca5b28f827d", "family": "50f227937e40", "family_version": 2, "instructions": {"s1": "65e08514335a"}, "policies": "dd7493315e65", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo-seam/0, canary/solo-seam/1, canary/solo-seam/10, canary/solo-seam/11, canary/solo-seam/12, canary/solo-seam/13, canary/solo-seam/14, canary/solo-seam/15, canary/solo-seam/16, canary/solo-seam/17, canary/solo-seam/18, canary/solo-seam/19, canary/solo-seam/2, canary/solo-seam/20, canary/solo-seam/21, canary/solo-seam/22, canary/solo-seam/23, canary/solo-seam/3, canary/solo-seam/4, canary/solo-seam/5, canary/solo-seam/6, canary/solo-seam/7, canary/solo-seam/8, canary/solo-seam/9 | `{"extension": "cca5b28f827d", "family": "50f227937e40", "family_version": 2, "instructions": {"s1": "da629f1779e4", "s2": "7b1969c038b1", "s3": "01f2efb9c31b"}, "policies": "dd7493315e65", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/team/0, canary/team/1, canary/team/10, canary/team/11, canary/team/12, canary/team/13, canary/team/14, canary/team/15, canary/team/16, canary/team/17, canary/team/18, canary/team/19, canary/team/2, canary/team/20, canary/team/21, canary/team/22, canary/team/23, canary/team/3, canary/team/4, canary/team/5, canary/team/6, canary/team/7, canary/team/8, canary/team/9 | `{"extension": "cca5b28f827d", "family": "50f227937e40", "family_version": 2, "instructions": {"s1": "fd49db66171c", "s2": "6a9c6edef5e1", "s3": "b4392c391a55"}, "policies": "dd7493315e65", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/hint/0, canary/hint/1, canary/hint/10, canary/hint/11, canary/hint/12, canary/hint/13, canary/hint/14, canary/hint/15, canary/hint/16, canary/hint/17, canary/hint/18, canary/hint/19, canary/hint/2, canary/hint/20, canary/hint/21, canary/hint/22, canary/hint/23, canary/hint/3, canary/hint/4, canary/hint/5, canary/hint/6, canary/hint/7, canary/hint/8, canary/hint/9 | `{"extension": "cca5b28f827d", "family": "50f227937e40", "family_version": 2, "instructions": {"s1": "1b8bbba95e49", "s2": "5d23c0ccc9f2", "s3": "9699e3344954"}, "policies": "dd7493315e65", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/twin/0, canary/twin/1, canary/twin/10, canary/twin/11, canary/twin/12, canary/twin/13, canary/twin/14, canary/twin/15, canary/twin/16, canary/twin/17, canary/twin/18, canary/twin/19, canary/twin/2, canary/twin/20, canary/twin/21, canary/twin/22, canary/twin/23, canary/twin/3, canary/twin/4, canary/twin/5, canary/twin/6, canary/twin/7, canary/twin/8, canary/twin/9 | `{"extension": "cca5b28f827d", "family": "50f227937e40", "family_version": 2, "instructions": {"s1": "0fab83ef4c22", "s2": "bdaff5028683", "s3": "0434f10949f7"}, "policies": "dd7493315e65", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/team-record/0, canary/team-record/1, canary/team-record/10, canary/team-record/11, canary/team-record/12, canary/team-record/13, canary/team-record/14, canary/team-record/15, canary/team-record/16, canary/team-record/17, canary/team-record/18, canary/team-record/19, canary/team-record/2, canary/team-record/20, canary/team-record/21, canary/team-record/22, canary/team-record/23, canary/team-record/3, canary/team-record/4, canary/team-record/5, canary/team-record/6, canary/team-record/7, canary/team-record/8, canary/team-record/9 | `{"extension": "cca5b28f827d", "family": "50f227937e40", "family_version": 2, "instructions": {"s1": "e425b85b1e70", "s2": "4fb8cf3e3da4", "s3": "504003195e73"}, "policies": "dd7493315e65", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/team-fullrecord/0, canary/team-fullrecord/1, canary/team-fullrecord/10, canary/team-fullrecord/11, canary/team-fullrecord/12, canary/team-fullrecord/13, canary/team-fullrecord/14, canary/team-fullrecord/15, canary/team-fullrecord/16, canary/team-fullrecord/17, canary/team-fullrecord/18, canary/team-fullrecord/19, canary/team-fullrecord/2, canary/team-fullrecord/20, canary/team-fullrecord/21, canary/team-fullrecord/22, canary/team-fullrecord/23, canary/team-fullrecord/3, canary/team-fullrecord/4, canary/team-fullrecord/5, canary/team-fullrecord/6, canary/team-fullrecord/7, canary/team-fullrecord/8, canary/team-fullrecord/9 | `{"extension": "cca5b28f827d", "family": "50f227937e40", "family_version": 2, "instructions": {"s1": "3280a5e514b9", "s2": "d44d3e74ae81", "s3": "5382906bfb8b"}, "policies": "dd7493315e65", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/probe-hunt-control/0, canary/probe-hunt-control/1, canary/probe-hunt-control/10, canary/probe-hunt-control/11, canary/probe-hunt-control/12, canary/probe-hunt-control/13, canary/probe-hunt-control/14, canary/probe-hunt-control/15, canary/probe-hunt-control/16, canary/probe-hunt-control/17, canary/probe-hunt-control/18, canary/probe-hunt-control/19, canary/probe-hunt-control/2, canary/probe-hunt-control/20, canary/probe-hunt-control/21, canary/probe-hunt-control/22, canary/probe-hunt-control/23, canary/probe-hunt-control/3, canary/probe-hunt-control/4, canary/probe-hunt-control/5, canary/probe-hunt-control/6, canary/probe-hunt-control/7, canary/probe-hunt-control/8, canary/probe-hunt-control/9, canary/probe-hunt-trap/0, canary/probe-hunt-trap/1, canary/probe-hunt-trap/10, canary/probe-hunt-trap/11, canary/probe-hunt-trap/12, canary/probe-hunt-trap/13, canary/probe-hunt-trap/14, canary/probe-hunt-trap/15, canary/probe-hunt-trap/16, canary/probe-hunt-trap/17, canary/probe-hunt-trap/18, canary/probe-hunt-trap/19, canary/probe-hunt-trap/2, canary/probe-hunt-trap/20, canary/probe-hunt-trap/21, canary/probe-hunt-trap/22, canary/probe-hunt-trap/23, canary/probe-hunt-trap/3, canary/probe-hunt-trap/4, canary/probe-hunt-trap/5, canary/probe-hunt-trap/6, canary/probe-hunt-trap/7, canary/probe-hunt-trap/8, canary/probe-hunt-trap/9, canary/probe-ref-control/0, canary/probe-ref-control/1, canary/probe-ref-control/10, canary/probe-ref-control/11, canary/probe-ref-control/12, canary/probe-ref-control/13, canary/probe-ref-control/14, canary/probe-ref-control/15, canary/probe-ref-control/16, canary/probe-ref-control/17, canary/probe-ref-control/18, canary/probe-ref-control/19, canary/probe-ref-control/2, canary/probe-ref-control/20, canary/probe-ref-control/21, canary/probe-ref-control/22, canary/probe-ref-control/23, canary/probe-ref-control/3, canary/probe-ref-control/4, canary/probe-ref-control/5, canary/probe-ref-control/6, canary/probe-ref-control/7, canary/probe-ref-control/8, canary/probe-ref-control/9, canary/probe-ref-trap/0, canary/probe-ref-trap/1, canary/probe-ref-trap/10, canary/probe-ref-trap/11, canary/probe-ref-trap/12, canary/probe-ref-trap/13, canary/probe-ref-trap/14, canary/probe-ref-trap/15, canary/probe-ref-trap/16, canary/probe-ref-trap/17, canary/probe-ref-trap/18, canary/probe-ref-trap/19, canary/probe-ref-trap/2, canary/probe-ref-trap/20, canary/probe-ref-trap/21, canary/probe-ref-trap/22, canary/probe-ref-trap/23, canary/probe-ref-trap/3, canary/probe-ref-trap/4, canary/probe-ref-trap/5, canary/probe-ref-trap/6, canary/probe-ref-trap/7, canary/probe-ref-trap/8, canary/probe-ref-trap/9 | `{"extension": "cca5b28f827d", "family": "50f227937e40", "family_version": 2, "instructions": {"s1": "905bf5f95b45", "s2": "6a9c6edef5e1", "s3": "905bf5f95b45"}, "policies": "dd7493315e65", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo/0, standard/solo/1, standard/solo/10, standard/solo/11, standard/solo/12, standard/solo/13, standard/solo/14, standard/solo/15, standard/solo/16, standard/solo/17, standard/solo/18, standard/solo/19, standard/solo/2, standard/solo/20, standard/solo/21, standard/solo/22, standard/solo/23, standard/solo/3, standard/solo/4, standard/solo/5, standard/solo/6, standard/solo/7, standard/solo/8, standard/solo/9 | `{"extension": "cca5b28f827d", "family": "50f227937e40", "family_version": 2, "instructions": {"s1": "e39aca868309"}, "policies": "dd7493315e65", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo-seam/0, standard/solo-seam/1, standard/solo-seam/10, standard/solo-seam/11, standard/solo-seam/12, standard/solo-seam/13, standard/solo-seam/14, standard/solo-seam/15, standard/solo-seam/16, standard/solo-seam/17, standard/solo-seam/18, standard/solo-seam/19, standard/solo-seam/2, standard/solo-seam/20, standard/solo-seam/21, standard/solo-seam/22, standard/solo-seam/23, standard/solo-seam/3, standard/solo-seam/4, standard/solo-seam/5, standard/solo-seam/6, standard/solo-seam/7, standard/solo-seam/8, standard/solo-seam/9 | `{"extension": "cca5b28f827d", "family": "50f227937e40", "family_version": 2, "instructions": {"s1": "42f16f44083c", "s2": "7cfdf389358f", "s3": "ea48cd203592", "s4": "c56c09a6f93d"}, "policies": "dd7493315e65", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/team/0, standard/team/1, standard/team/10, standard/team/11, standard/team/12, standard/team/13, standard/team/14, standard/team/15, standard/team/16, standard/team/17, standard/team/18, standard/team/19, standard/team/2, standard/team/20, standard/team/21, standard/team/22, standard/team/23, standard/team/3, standard/team/4, standard/team/5, standard/team/6, standard/team/7, standard/team/8, standard/team/9 | `{"extension": "cca5b28f827d", "family": "50f227937e40", "family_version": 2, "instructions": {"s1": "3658b4cd6dd4", "s2": "cf89bb444e16", "s3": "a9e50ebb09b5", "s4": "63d82da4c358"}, "policies": "dd7493315e65", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/hint/0, standard/hint/1, standard/hint/10, standard/hint/11, standard/hint/12, standard/hint/13, standard/hint/14, standard/hint/15, standard/hint/16, standard/hint/17, standard/hint/18, standard/hint/19, standard/hint/2, standard/hint/20, standard/hint/21, standard/hint/22, standard/hint/23, standard/hint/3, standard/hint/4, standard/hint/5, standard/hint/6, standard/hint/7, standard/hint/8, standard/hint/9 | `{"extension": "cca5b28f827d", "family": "50f227937e40", "family_version": 2, "instructions": {"s1": "1890ecb30fa0", "s2": "1fe415bb4eb6", "s3": "6150c4a382cc", "s4": "eb4342754ad0"}, "policies": "dd7493315e65", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/twin/0, standard/twin/1, standard/twin/10, standard/twin/11, standard/twin/12, standard/twin/13, standard/twin/14, standard/twin/15, standard/twin/16, standard/twin/17, standard/twin/18, standard/twin/19, standard/twin/2, standard/twin/20, standard/twin/21, standard/twin/22, standard/twin/23, standard/twin/3, standard/twin/4, standard/twin/5, standard/twin/6, standard/twin/7, standard/twin/8, standard/twin/9 | `{"extension": "cca5b28f827d", "family": "50f227937e40", "family_version": 2, "instructions": {"s1": "a771f37d4e8c", "s2": "6c06264406c5", "s3": "de286d139031", "s4": "308c1948f9a9"}, "policies": "dd7493315e65", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/team-record/0, standard/team-record/1, standard/team-record/10, standard/team-record/11, standard/team-record/12, standard/team-record/13, standard/team-record/14, standard/team-record/15, standard/team-record/16, standard/team-record/17, standard/team-record/18, standard/team-record/19, standard/team-record/2, standard/team-record/20, standard/team-record/21, standard/team-record/22, standard/team-record/23, standard/team-record/3, standard/team-record/4, standard/team-record/5, standard/team-record/6, standard/team-record/7, standard/team-record/8, standard/team-record/9 | `{"extension": "cca5b28f827d", "family": "50f227937e40", "family_version": 2, "instructions": {"s1": "cbb92738beed", "s2": "c554356ca1f3", "s3": "2ac01ecf563e", "s4": "8ea78d70d277"}, "policies": "dd7493315e65", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/team-fullrecord/0, standard/team-fullrecord/1, standard/team-fullrecord/10, standard/team-fullrecord/11, standard/team-fullrecord/12, standard/team-fullrecord/13, standard/team-fullrecord/14, standard/team-fullrecord/15, standard/team-fullrecord/16, standard/team-fullrecord/17, standard/team-fullrecord/18, standard/team-fullrecord/19, standard/team-fullrecord/2, standard/team-fullrecord/20, standard/team-fullrecord/21, standard/team-fullrecord/22, standard/team-fullrecord/23, standard/team-fullrecord/3, standard/team-fullrecord/4, standard/team-fullrecord/5, standard/team-fullrecord/6, standard/team-fullrecord/7, standard/team-fullrecord/8, standard/team-fullrecord/9 | `{"extension": "cca5b28f827d", "family": "50f227937e40", "family_version": 2, "instructions": {"s1": "0b981f82f185", "s2": "1a8f1d18865d", "s3": "9d543515e783", "s4": "a04b8c1dcec7"}, "policies": "dd7493315e65", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/probe-hunt-control/0, standard/probe-hunt-control/1, standard/probe-hunt-control/10, standard/probe-hunt-control/11, standard/probe-hunt-control/12, standard/probe-hunt-control/13, standard/probe-hunt-control/14, standard/probe-hunt-control/15, standard/probe-hunt-control/16, standard/probe-hunt-control/17, standard/probe-hunt-control/18, standard/probe-hunt-control/19, standard/probe-hunt-control/2, standard/probe-hunt-control/20, standard/probe-hunt-control/21, standard/probe-hunt-control/22, standard/probe-hunt-control/23, standard/probe-hunt-control/3, standard/probe-hunt-control/4, standard/probe-hunt-control/5, standard/probe-hunt-control/6, standard/probe-hunt-control/7, standard/probe-hunt-control/8, standard/probe-hunt-control/9, standard/probe-hunt-trap/0, standard/probe-hunt-trap/1, standard/probe-hunt-trap/10, standard/probe-hunt-trap/11, standard/probe-hunt-trap/12, standard/probe-hunt-trap/13, standard/probe-hunt-trap/14, standard/probe-hunt-trap/15, standard/probe-hunt-trap/16, standard/probe-hunt-trap/17, standard/probe-hunt-trap/18, standard/probe-hunt-trap/19, standard/probe-hunt-trap/2, standard/probe-hunt-trap/20, standard/probe-hunt-trap/21, standard/probe-hunt-trap/22, standard/probe-hunt-trap/23, standard/probe-hunt-trap/3, standard/probe-hunt-trap/4, standard/probe-hunt-trap/5, standard/probe-hunt-trap/6, standard/probe-hunt-trap/7, standard/probe-hunt-trap/8, standard/probe-hunt-trap/9, standard/probe-ref-control/0, standard/probe-ref-control/1, standard/probe-ref-control/10, standard/probe-ref-control/11, standard/probe-ref-control/12, standard/probe-ref-control/13, standard/probe-ref-control/14, standard/probe-ref-control/15, standard/probe-ref-control/16, standard/probe-ref-control/17, standard/probe-ref-control/18, standard/probe-ref-control/19, standard/probe-ref-control/2, standard/probe-ref-control/20, standard/probe-ref-control/21, standard/probe-ref-control/22, standard/probe-ref-control/23, standard/probe-ref-control/3, standard/probe-ref-control/4, standard/probe-ref-control/5, standard/probe-ref-control/6, standard/probe-ref-control/7, standard/probe-ref-control/8, standard/probe-ref-control/9, standard/probe-ref-trap/0, standard/probe-ref-trap/1, standard/probe-ref-trap/10, standard/probe-ref-trap/11, standard/probe-ref-trap/12, standard/probe-ref-trap/13, standard/probe-ref-trap/14, standard/probe-ref-trap/15, standard/probe-ref-trap/16, standard/probe-ref-trap/17, standard/probe-ref-trap/18, standard/probe-ref-trap/19, standard/probe-ref-trap/2, standard/probe-ref-trap/20, standard/probe-ref-trap/21, standard/probe-ref-trap/22, standard/probe-ref-trap/23, standard/probe-ref-trap/3, standard/probe-ref-trap/4, standard/probe-ref-trap/5, standard/probe-ref-trap/6, standard/probe-ref-trap/7, standard/probe-ref-trap/8, standard/probe-ref-trap/9 | `{"extension": "cca5b28f827d", "family": "50f227937e40", "family_version": 2, "instructions": {"s1": "905bf5f95b45", "s2": "905bf5f95b45", "s3": "a9e50ebb09b5", "s4": "905bf5f95b45"}, "policies": "dd7493315e65", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo/0, heavy/solo/1, heavy/solo/10, heavy/solo/11, heavy/solo/12, heavy/solo/13, heavy/solo/14, heavy/solo/15, heavy/solo/16, heavy/solo/17, heavy/solo/18, heavy/solo/19, heavy/solo/2, heavy/solo/20, heavy/solo/21, heavy/solo/22, heavy/solo/23, heavy/solo/3, heavy/solo/4, heavy/solo/5, heavy/solo/6, heavy/solo/7, heavy/solo/8, heavy/solo/9 | `{"extension": "cca5b28f827d", "family": "50f227937e40", "family_version": 2, "instructions": {"s1": "8f8366a17f0c"}, "policies": "dd7493315e65", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo-seam/0, heavy/solo-seam/1, heavy/solo-seam/10, heavy/solo-seam/11, heavy/solo-seam/12, heavy/solo-seam/13, heavy/solo-seam/14, heavy/solo-seam/15, heavy/solo-seam/16, heavy/solo-seam/17, heavy/solo-seam/18, heavy/solo-seam/19, heavy/solo-seam/2, heavy/solo-seam/20, heavy/solo-seam/21, heavy/solo-seam/22, heavy/solo-seam/23, heavy/solo-seam/3, heavy/solo-seam/4, heavy/solo-seam/5, heavy/solo-seam/6, heavy/solo-seam/7, heavy/solo-seam/8, heavy/solo-seam/9 | `{"extension": "cca5b28f827d", "family": "50f227937e40", "family_version": 2, "instructions": {"s1": "50ff7b937342", "s2": "94207d75c862", "s3": "a28d211bd546", "s4": "1c2b64a13d4e", "s5": "6f8ff01d87a1", "s6": "ddc3f3880ebf", "s7": "809e1713e535", "s8": "5a23ba2b922b"}, "policies": "dd7493315e65", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/team/0, heavy/team/1, heavy/team/10, heavy/team/11, heavy/team/12, heavy/team/13, heavy/team/14, heavy/team/15, heavy/team/16, heavy/team/17, heavy/team/18, heavy/team/19, heavy/team/2, heavy/team/20, heavy/team/21, heavy/team/22, heavy/team/23, heavy/team/3, heavy/team/4, heavy/team/5, heavy/team/6, heavy/team/7, heavy/team/8, heavy/team/9 | `{"extension": "cca5b28f827d", "family": "50f227937e40", "family_version": 2, "instructions": {"s1": "d90e94bad4d7", "s2": "a406bc061ab2", "s3": "ed4697dc3f59", "s4": "2a1a8eac3a1d", "s5": "6ca6342f70de", "s6": "b5d723917e62", "s7": "60fb51e7a50c", "s8": "24f1f210a8f3"}, "policies": "dd7493315e65", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/hint/0, heavy/hint/1, heavy/hint/10, heavy/hint/11, heavy/hint/12, heavy/hint/13, heavy/hint/14, heavy/hint/15, heavy/hint/16, heavy/hint/17, heavy/hint/18, heavy/hint/19, heavy/hint/2, heavy/hint/20, heavy/hint/21, heavy/hint/22, heavy/hint/23, heavy/hint/3, heavy/hint/4, heavy/hint/5, heavy/hint/6, heavy/hint/7, heavy/hint/8, heavy/hint/9 | `{"extension": "cca5b28f827d", "family": "50f227937e40", "family_version": 2, "instructions": {"s1": "df522c0abde8", "s2": "708f48e8fc36", "s3": "b44f1ef91e0f", "s4": "7ed52b85127f", "s5": "1a0beaf4ecc4", "s6": "c8315279682f", "s7": "e6862b552d47", "s8": "a524f56a6e9d"}, "policies": "dd7493315e65", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/twin/0, heavy/twin/1, heavy/twin/10, heavy/twin/11, heavy/twin/12, heavy/twin/13, heavy/twin/14, heavy/twin/15, heavy/twin/16, heavy/twin/17, heavy/twin/18, heavy/twin/19, heavy/twin/2, heavy/twin/20, heavy/twin/21, heavy/twin/22, heavy/twin/23, heavy/twin/3, heavy/twin/4, heavy/twin/5, heavy/twin/6, heavy/twin/7, heavy/twin/8, heavy/twin/9 | `{"extension": "cca5b28f827d", "family": "50f227937e40", "family_version": 2, "instructions": {"s1": "20a7f3bdbdb6", "s2": "fe8a30d83db0", "s3": "01c5dab31a77", "s4": "4cc902d43cdb", "s5": "382dcf9a08e6", "s6": "30595e9a54e0", "s7": "003b46b77aa6", "s8": "1d9af7de6ef6"}, "policies": "dd7493315e65", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/team-record/0, heavy/team-record/1, heavy/team-record/10, heavy/team-record/11, heavy/team-record/12, heavy/team-record/13, heavy/team-record/14, heavy/team-record/15, heavy/team-record/16, heavy/team-record/17, heavy/team-record/18, heavy/team-record/19, heavy/team-record/2, heavy/team-record/20, heavy/team-record/21, heavy/team-record/22, heavy/team-record/23, heavy/team-record/3, heavy/team-record/4, heavy/team-record/5, heavy/team-record/6, heavy/team-record/7, heavy/team-record/8, heavy/team-record/9 | `{"extension": "cca5b28f827d", "family": "50f227937e40", "family_version": 2, "instructions": {"s1": "f7a465a86cd2", "s2": "96b3e0dd9c26", "s3": "4d9ead89cd29", "s4": "c7cce6ead0f9", "s5": "a5e4758c6d82", "s6": "9e2921791135", "s7": "4726e9878906", "s8": "bdf29fe56e0d"}, "policies": "dd7493315e65", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/team-fullrecord/0, heavy/team-fullrecord/1, heavy/team-fullrecord/10, heavy/team-fullrecord/11, heavy/team-fullrecord/12, heavy/team-fullrecord/13, heavy/team-fullrecord/14, heavy/team-fullrecord/15, heavy/team-fullrecord/16, heavy/team-fullrecord/17, heavy/team-fullrecord/18, heavy/team-fullrecord/19, heavy/team-fullrecord/2, heavy/team-fullrecord/20, heavy/team-fullrecord/21, heavy/team-fullrecord/22, heavy/team-fullrecord/23, heavy/team-fullrecord/3, heavy/team-fullrecord/4, heavy/team-fullrecord/5, heavy/team-fullrecord/6, heavy/team-fullrecord/7, heavy/team-fullrecord/8, heavy/team-fullrecord/9 | `{"extension": "cca5b28f827d", "family": "50f227937e40", "family_version": 2, "instructions": {"s1": "7787c64aca80", "s2": "3f16ef5eca32", "s3": "4d0edec4e69e", "s4": "34e78cad00a0", "s5": "4eab63fce35f", "s6": "a25d79d9415d", "s7": "3136786052be", "s8": "ced4bcea7b42"}, "policies": "dd7493315e65", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/probe-hunt-control/0, heavy/probe-hunt-control/1, heavy/probe-hunt-control/10, heavy/probe-hunt-control/11, heavy/probe-hunt-control/12, heavy/probe-hunt-control/13, heavy/probe-hunt-control/14, heavy/probe-hunt-control/15, heavy/probe-hunt-control/16, heavy/probe-hunt-control/17, heavy/probe-hunt-control/18, heavy/probe-hunt-control/19, heavy/probe-hunt-control/2, heavy/probe-hunt-control/20, heavy/probe-hunt-control/21, heavy/probe-hunt-control/22, heavy/probe-hunt-control/23, heavy/probe-hunt-control/3, heavy/probe-hunt-control/4, heavy/probe-hunt-control/5, heavy/probe-hunt-control/6, heavy/probe-hunt-control/7, heavy/probe-hunt-control/8, heavy/probe-hunt-control/9, heavy/probe-hunt-trap/0, heavy/probe-hunt-trap/1, heavy/probe-hunt-trap/10, heavy/probe-hunt-trap/11, heavy/probe-hunt-trap/12, heavy/probe-hunt-trap/13, heavy/probe-hunt-trap/14, heavy/probe-hunt-trap/15, heavy/probe-hunt-trap/16, heavy/probe-hunt-trap/17, heavy/probe-hunt-trap/18, heavy/probe-hunt-trap/19, heavy/probe-hunt-trap/2, heavy/probe-hunt-trap/20, heavy/probe-hunt-trap/21, heavy/probe-hunt-trap/22, heavy/probe-hunt-trap/23, heavy/probe-hunt-trap/3, heavy/probe-hunt-trap/4, heavy/probe-hunt-trap/5, heavy/probe-hunt-trap/6, heavy/probe-hunt-trap/7, heavy/probe-hunt-trap/8, heavy/probe-hunt-trap/9, heavy/probe-ref-control/0, heavy/probe-ref-control/1, heavy/probe-ref-control/10, heavy/probe-ref-control/11, heavy/probe-ref-control/12, heavy/probe-ref-control/13, heavy/probe-ref-control/14, heavy/probe-ref-control/15, heavy/probe-ref-control/16, heavy/probe-ref-control/17, heavy/probe-ref-control/18, heavy/probe-ref-control/19, heavy/probe-ref-control/2, heavy/probe-ref-control/20, heavy/probe-ref-control/21, heavy/probe-ref-control/22, heavy/probe-ref-control/23, heavy/probe-ref-control/3, heavy/probe-ref-control/4, heavy/probe-ref-control/5, heavy/probe-ref-control/6, heavy/probe-ref-control/7, heavy/probe-ref-control/8, heavy/probe-ref-control/9, heavy/probe-ref-trap/0, heavy/probe-ref-trap/1, heavy/probe-ref-trap/10, heavy/probe-ref-trap/11, heavy/probe-ref-trap/12, heavy/probe-ref-trap/13, heavy/probe-ref-trap/14, heavy/probe-ref-trap/15, heavy/probe-ref-trap/16, heavy/probe-ref-trap/17, heavy/probe-ref-trap/18, heavy/probe-ref-trap/19, heavy/probe-ref-trap/2, heavy/probe-ref-trap/20, heavy/probe-ref-trap/21, heavy/probe-ref-trap/22, heavy/probe-ref-trap/23, heavy/probe-ref-trap/3, heavy/probe-ref-trap/4, heavy/probe-ref-trap/5, heavy/probe-ref-trap/6, heavy/probe-ref-trap/7, heavy/probe-ref-trap/8, heavy/probe-ref-trap/9 | `{"extension": "cca5b28f827d", "family": "50f227937e40", "family_version": 2, "instructions": {"s1": "905bf5f95b45", "s2": "905bf5f95b45", "s3": "905bf5f95b45", "s4": "905bf5f95b45", "s5": "905bf5f95b45", "s6": "905bf5f95b45", "s7": "60fb51e7a50c", "s8": "905bf5f95b45"}, "policies": "dd7493315e65", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |

## Load and cost (estimates)

Estimate: 4 characters per input token; repeat the instruction, JSON briefing and seat files once per simulated world request (at least once per model seat). Excludes scripted seats, system prompts, replies, history and output tokens. Requests are not measured model turns.

| Level / arm | Per-seat max calls / briefing chars | Mean estimated tokens | $1 / $5 / $15 per M |
|---|---|---|---|
| canary / solo | s1: 33/57 | 30360 | $0.03036 / $0.15180 / $0.45540 |
| canary / solo-seam | s1: 12/58, s2: 12/58, s3: 13/58 | 35298 | $0.03530 / $0.17649 / $0.52947 |
| canary / team | s1: 12/58, s2: 12/58, s3: 13/58 | 34854 | $0.03485 / $0.17427 / $0.52281 |
| canary / hint | s1: 12/58, s2: 12/58, s3: 13/58 | 35890 | $0.03589 / $0.17945 / $0.53835 |
| canary / twin | s1: 12/58, s2: 12/58, s3: 13/58 | 35705 | $0.03571 / $0.17852 / $0.53558 |
| canary / team-record | s1: 12/58, s2: 12/58, s3: 13/58 | 35631 | $0.03563 / $0.17816 / $0.53446 |
| canary / team-fullrecord | s1: 12/58, s2: 12/58, s3: 13/58 | 35853 | $0.03585 / $0.17927 / $0.53780 |
| canary / probe-hunt-control | s1: 12/58, s2: 12/58, s3: 13/58 | 11304 | $0.01130 / $0.05652 / $0.16956 |
| canary / probe-hunt-trap | s1: 12/58, s2: 12/58, s3: 13/58 | 11304 | $0.01130 / $0.05652 / $0.16956 |
| canary / probe-ref-control | s1: 12/58, s2: 12/58, s3: 13/58 | 11304 | $0.01130 / $0.05652 / $0.16956 |
| canary / probe-ref-trap | s1: 12/58, s2: 12/58, s3: 13/58 | 11304 | $0.01130 / $0.05652 / $0.16956 |
| standard / solo | s1: 43/57 | 39560 | $0.03956 / $0.19780 / $0.59340 |
| standard / solo-seam | s1: 12/58, s2: 12/58, s3: 12/58, s4: 13/58 | 46746 | $0.04675 / $0.23373 / $0.70119 |
| standard / team | s1: 12/58, s2: 12/58, s3: 12/58, s4: 13/58 | 46158 | $0.04616 / $0.23079 / $0.69237 |
| standard / hint | s1: 12/58, s2: 12/58, s3: 12/58, s4: 13/58 | 47530 | $0.04753 / $0.23765 / $0.71295 |
| standard / twin | s1: 12/58, s2: 12/58, s3: 12/58, s4: 13/58 | 47285 | $0.04729 / $0.23642 / $0.70927 |
| standard / team-record | s1: 12/58, s2: 12/58, s3: 12/58, s4: 13/58 | 47187 | $0.04719 / $0.23594 / $0.70781 |
| standard / team-fullrecord | s1: 12/58, s2: 12/58, s3: 12/58, s4: 13/58 | 47481 | $0.04748 / $0.23741 / $0.71222 |
| standard / probe-hunt-control | s1: 12/58, s2: 12/58, s3: 12/58, s4: 13/58 | 11304 | $0.01130 / $0.05652 / $0.16956 |
| standard / probe-hunt-trap | s1: 12/58, s2: 12/58, s3: 12/58, s4: 13/58 | 11304 | $0.01130 / $0.05652 / $0.16956 |
| standard / probe-ref-control | s1: 12/58, s2: 12/58, s3: 12/58, s4: 13/58 | 11304 | $0.01130 / $0.05652 / $0.16956 |
| standard / probe-ref-trap | s1: 12/58, s2: 12/58, s3: 12/58, s4: 13/58 | 11304 | $0.01130 / $0.05652 / $0.16956 |
| heavy / solo | s1: 83/57 | 76360 | $0.07636 / $0.38180 / $1.14540 |
| heavy / solo-seam | s1: 12/58, s2: 12/58, s3: 12/58, s4: 12/58, s5: 12/58, s6: 12/58, s7: 12/58, s8: 13/58 | 92538 | $0.09254 / $0.46269 / $1.38807 |
| heavy / team | s1: 12/58, s2: 12/58, s3: 12/58, s4: 12/58, s5: 12/58, s6: 12/58, s7: 12/58, s8: 13/58 | 91374 | $0.09137 / $0.45687 / $1.37061 |
| heavy / hint | s1: 12/58, s2: 12/58, s3: 12/58, s4: 12/58, s5: 12/58, s6: 12/58, s7: 12/58, s8: 13/58 | 94090 | $0.09409 / $0.47045 / $1.41135 |
| heavy / twin | s1: 12/58, s2: 12/58, s3: 12/58, s4: 12/58, s5: 12/58, s6: 12/58, s7: 12/58, s8: 13/58 | 93605 | $0.09360 / $0.46803 / $1.40407 |
| heavy / team-record | s1: 12/58, s2: 12/58, s3: 12/58, s4: 12/58, s5: 12/58, s6: 12/58, s7: 12/58, s8: 13/58 | 93411 | $0.09341 / $0.46705 / $1.40116 |
| heavy / team-fullrecord | s1: 12/58, s2: 12/58, s3: 12/58, s4: 12/58, s5: 12/58, s6: 12/58, s7: 12/58, s8: 13/58 | 93993 | $0.09399 / $0.46997 / $1.40989 |
| heavy / probe-hunt-control | s1: 12/58, s2: 12/58, s3: 12/58, s4: 12/58, s5: 12/58, s6: 12/58, s7: 12/58, s8: 13/58 | 11304 | $0.01130 / $0.05652 / $0.16956 |
| heavy / probe-hunt-trap | s1: 12/58, s2: 12/58, s3: 12/58, s4: 12/58, s5: 12/58, s6: 12/58, s7: 12/58, s8: 13/58 | 11304 | $0.01130 / $0.05652 / $0.16956 |
| heavy / probe-ref-control | s1: 12/58, s2: 12/58, s3: 12/58, s4: 12/58, s5: 12/58, s6: 12/58, s7: 12/58, s8: 13/58 | 11304 | $0.01130 / $0.05652 / $0.16956 |
| heavy / probe-ref-trap | s1: 12/58, s2: 12/58, s3: 12/58, s4: 12/58, s5: 12/58, s6: 12/58, s7: 12/58, s8: 13/58 | 11304 | $0.01130 / $0.05652 / $0.16956 |

## Evidence and predictions

[flotilla/docs/seams/handoff.md](file:///Users/petermakhnatch/Developer/flotilla-wt/seams-handoff/flotilla/docs/seams/handoff.md)

## Open risks

- None flagged.

Scripted policy results are not estimates of frontier-model performance.
