# EvalCard: tell — Warehouse receiving and dispatch

**Seam:** A receiving desk must route its private supplier notice to the desk whose carrier needs it.
**Kit commit:** `2ca01cac16fc20abf4a47bff4906998a1104dbc8`; variant: `default`.
**Arms:** solo, solo-control, team, team-control, probe-trap, probe-control, solo-seam, solo-seam-control, auto, auto-control, hint, hint-control. Levels: canary (canary), standard (standard), heavy (heavy).
**Sweep:** 24 instances × 3 schedule seeds. No model calls.
**Policies unavailable:** blind, script-loop.

## Policy matrix (mean / min / n; invalid count in parentheses)

| Level | Arm | Seats | careful | naive | nop | random | lone | lapse-10 | lapse-20 | lapse-30 | lapse-50 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| canary | solo | p1 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| canary | solo-control | p1 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| canary | team | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.792 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.958 / 0.000 / 72 (0) | 0.833 / 0.000 / 72 (0) | 0.625 / 0.000 / 72 (0) | 0.375 / 0.000 / 72 (0) |
| canary | team-control | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| canary | probe-trap | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.792 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.958 / 0.000 / 72 (0) | 0.833 / 0.000 / 72 (0) | 0.625 / 0.000 / 72 (0) | 0.375 / 0.000 / 72 (0) |
| canary | probe-control | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| canary | solo-seam | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.792 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.958 / 0.000 / 72 (0) | 0.833 / 0.000 / 72 (0) | 0.625 / 0.000 / 72 (0) | 0.375 / 0.000 / 72 (0) |
| canary | solo-seam-control | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| canary | auto | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| canary | auto-control | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| canary | hint | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.792 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.958 / 0.000 / 72 (0) | 0.833 / 0.000 / 72 (0) | 0.625 / 0.000 / 72 (0) | 0.375 / 0.000 / 72 (0) |
| canary | hint-control | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard | solo | p1 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard | solo-control | p1 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard | team | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.375 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.958 / 0.000 / 72 (0) | 0.833 / 0.000 / 72 (0) | 0.750 / 0.000 / 72 (0) | 0.750 / 0.000 / 72 (0) |
| standard | team-control | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard | probe-trap | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.375 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.958 / 0.000 / 72 (0) | 0.833 / 0.000 / 72 (0) | 0.750 / 0.000 / 72 (0) | 0.750 / 0.000 / 72 (0) |
| standard | probe-control | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard | solo-seam | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.375 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.958 / 0.000 / 72 (0) | 0.833 / 0.000 / 72 (0) | 0.750 / 0.000 / 72 (0) | 0.750 / 0.000 / 72 (0) |
| standard | solo-seam-control | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard | auto | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard | auto-control | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard | hint | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.375 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.958 / 0.000 / 72 (0) | 0.833 / 0.000 / 72 (0) | 0.750 / 0.000 / 72 (0) | 0.750 / 0.000 / 72 (0) |
| standard | hint-control | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy | solo | p1 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy | solo-control | p1 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy | team | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.417 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.708 / 0.000 / 72 (0) | 0.750 / 0.000 / 72 (0) | 0.542 / 0.000 / 72 (0) |
| heavy | team-control | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy | probe-trap | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.417 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.708 / 0.000 / 72 (0) | 0.750 / 0.000 / 72 (0) | 0.542 / 0.000 / 72 (0) |
| heavy | probe-control | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy | solo-seam | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.417 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.708 / 0.000 / 72 (0) | 0.750 / 0.000 / 72 (0) | 0.542 / 0.000 / 72 (0) |
| heavy | solo-seam-control | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy | auto | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.750 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy | auto-control | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy | hint | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.417 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.708 / 0.000 / 72 (0) | 0.750 / 0.000 / 72 (0) | 0.542 / 0.000 / 72 (0) |
| heavy | hint-control | p1, p2, p3 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |

## Checks

| Check | Standard | Result | Evidence |
|---|---|---|---|
| levels | A5 | **PASS** | All requested dose levels available |
| solvable | A2/B1 | **PASS** | 2592 runs; careful reward = 1 |
| easy alone | A2 | **PASS** | 216 runs; solo careful reward = 1 |
| floor | A3 | **PASS** | 2592 runs; every nop reward ≤ 0.05 |
| chance | A3 | **PASS** | canary/team/random: actual 0.7916666666666666, expected 0.7917 (absolute tolerance 0.1); canary/probe-trap/random: actual 0.7916666666666666, expected 0.7917 (absolute tolerance 0.1); standard/team/random: actual 0.375, expected 0.3750 (absolute tolerance 0.1); standard/probe-trap/random: actual 0.375, expected 0.3750 (absolute tolerance 0.1); heavy/team/random: actual 0.4166666666666667, expec… (full evidence in JSON) |
| separation | A4 | **PASS** | canary/probe-trap: gap 1.000; canary/auto-control: naive min 1.0; canary/hint-control: naive min 1.0; canary/probe-control: naive min 1.0; canary/solo-control: naive min 1.0; canary/solo-seam-control: naive min 1.0; canary/team-control: naive min 1.0; standard/probe-trap: gap 1.000; standard/auto-control: naive min 1.0; standard/hint-control: naive min 1.0; standard/probe-control: naive min 1.0… (full evidence in JSON) |
| dose response | A5/I1 | **PASS** | canary → standard → heavy: [0.0, 0.0, 0.0] |
| one seat is not enough | A3/C1 | **PASS** | 216 runs; every lone team reward ≤ 0.5 |
| schedule independence | E3 | **PASS** | 2592 runs; careful scores 1 across 3 schedule seed(s); max within-instance naive spread: 0.0 |
| simulation validity | F1 | **PASS** | 23328 runs; No invalid runs in the ordinary sweep |
| determinism | E1/E2 | **PASS** | 72/72 sample event hashes match |
| no leak | C1/D2 | **PASS** | 864 built tasks; seat views and family QA scanned |
| pairs | A1/D2 | **PASS** | auto→auto-control: 72 pairs, max 0 changed chars; fields: none; hint→hint-control: 72 pairs, max 0 changed chars; fields: none; probe-control→auto-control: 72 pairs, max 104 changed chars; fields: p1/instruction; probe-control→solo-seam-control: 72 pairs, max 58 changed chars; fields: p1/instruction; probe-trap→auto: 72 pairs, max 104 changed chars; fields: p1/instruction; probe-trap→probe-cont… (full evidence in JSON) |
| twin rule | A1 | **PASS** | Every team/trap has a declared twin |
| invalid is not zero | F1 | **PASS** | Budget-limited simulation: stalled=True, invalid=True, reward=None |
| canary | A6 | **PASS** | Marker checked in 864 task files and all seat views |
| power plan | A3 | **PASS** | 24 paired comparisons; fixed exact McNemar plan (see table) |

## Three costs (paired reward differences)

Positive means the control scores higher than team; missing arms are NA.

| Level | Policy | Cost | Control − team | Effect | Valid pairs / instances | Excluded |
|---|---|---|---|---|---|---|
| canary | careful | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| canary | careful | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | careful | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | naive | boundary | solo − team | +1.000 | 72 / 24 | 0 |
| canary | naive | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | naive | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | nop | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| canary | nop | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | nop | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | random | boundary | solo − team | +0.208 | 72 / 24 | 0 |
| canary | random | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | random | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | lone | boundary | solo − team | +1.000 | 72 / 24 | 0 |
| canary | lone | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | lone | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-10 | boundary | solo − team | +0.042 | 72 / 24 | 0 |
| canary | lapse-10 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-10 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-20 | boundary | solo − team | +0.167 | 72 / 24 | 0 |
| canary | lapse-20 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-20 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-30 | boundary | solo − team | +0.375 | 72 / 24 | 0 |
| canary | lapse-30 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-30 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-50 | boundary | solo − team | +0.625 | 72 / 24 | 0 |
| canary | lapse-50 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-50 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | careful | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| standard | careful | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | careful | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | naive | boundary | solo − team | +1.000 | 72 / 24 | 0 |
| standard | naive | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | naive | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | nop | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| standard | nop | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | nop | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | random | boundary | solo − team | +0.625 | 72 / 24 | 0 |
| standard | random | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | random | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | lone | boundary | solo − team | +1.000 | 72 / 24 | 0 |
| standard | lone | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | lone | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-10 | boundary | solo − team | +0.042 | 72 / 24 | 0 |
| standard | lapse-10 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-10 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-20 | boundary | solo − team | +0.167 | 72 / 24 | 0 |
| standard | lapse-20 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-20 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-30 | boundary | solo − team | +0.250 | 72 / 24 | 0 |
| standard | lapse-30 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-30 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-50 | boundary | solo − team | +0.250 | 72 / 24 | 0 |
| standard | lapse-50 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-50 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | careful | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| heavy | careful | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | careful | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | naive | boundary | solo − team | +1.000 | 72 / 24 | 0 |
| heavy | naive | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | naive | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | nop | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| heavy | nop | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | nop | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | random | boundary | solo − team | +0.583 | 72 / 24 | 0 |
| heavy | random | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | random | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | lone | boundary | solo − team | +1.000 | 72 / 24 | 0 |
| heavy | lone | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | lone | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | lapse-10 | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| heavy | lapse-10 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | lapse-10 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | lapse-20 | boundary | solo − team | +0.292 | 72 / 24 | 0 |
| heavy | lapse-20 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | lapse-20 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | lapse-30 | boundary | solo − team | +0.250 | 72 / 24 | 0 |
| heavy | lapse-30 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | lapse-30 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | lapse-50 | boundary | solo − team | +0.458 | 72 / 24 | 0 |
| heavy | lapse-50 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | lapse-50 | protocol | hint − team | +0.000 | 72 / 24 | 0 |

## Power plan (scripted stand-ins)

Fixed sample, exact two-sided McNemar, α=0.05, target power 0.8; binary success means reward = 1. The paired reward effect also includes partial credit.
Schedule repeats estimate outcome probabilities; they are not independent instances. The planned n assumes independent new instances, one paired run each.
No-effect and NA cells have no planned n. Plans search up to 1,000 instances. These are sensitivity estimates, not model performance predictions.

| Level | Policy | Control − treatment | Reward effect | Success effect | + / − | Valid pairs / instances | Excluded | Planned n |
|---|---|---|---|---|---|---|---|---|
| canary | lapse-10 | probe-control − probe-trap | +0.042 | +0.042 | 3 / 0 | 72 / 24 | 0 | 189 |
| canary | lapse-10 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| canary | lapse-20 | probe-control − probe-trap | +0.167 | +0.167 | 12 / 0 | 72 / 24 | 0 | 46 |
| canary | lapse-20 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| canary | lapse-30 | probe-control − probe-trap | +0.375 | +0.375 | 27 / 0 | 72 / 24 | 0 | 20 |
| canary | lapse-30 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| canary | lapse-50 | probe-control − probe-trap | +0.625 | +0.625 | 45 / 0 | 72 / 24 | 0 | 11 |
| canary | lapse-50 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| standard | lapse-10 | probe-control − probe-trap | +0.042 | +0.042 | 3 / 0 | 72 / 24 | 0 | 189 |
| standard | lapse-10 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| standard | lapse-20 | probe-control − probe-trap | +0.167 | +0.167 | 12 / 0 | 72 / 24 | 0 | 46 |
| standard | lapse-20 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| standard | lapse-30 | probe-control − probe-trap | +0.250 | +0.250 | 18 / 0 | 72 / 24 | 0 | 31 |
| standard | lapse-30 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| standard | lapse-50 | probe-control − probe-trap | +0.250 | +0.250 | 18 / 0 | 72 / 24 | 0 | 31 |
| standard | lapse-50 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| heavy | lapse-10 | probe-control − probe-trap | +0.000 | +0.000 | 0 / 0 | 72 / 24 | 0 | no effect |
| heavy | lapse-10 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| heavy | lapse-20 | probe-control − probe-trap | +0.292 | +0.292 | 21 / 0 | 72 / 24 | 0 | 26 |
| heavy | lapse-20 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| heavy | lapse-30 | probe-control − probe-trap | +0.250 | +0.250 | 18 / 0 | 72 / 24 | 0 | 31 |
| heavy | lapse-30 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| heavy | lapse-50 | probe-control − probe-trap | +0.458 | +0.458 | 33 / 0 | 72 / 24 | 0 | 16 |
| heavy | lapse-50 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |

## Versions

| Task(s) | Simulator versions |
|---|---|
| canary/solo-control/0, canary/solo-control/1, canary/solo-control/10, canary/solo-control/11, canary/solo-control/12, canary/solo-control/13, canary/solo-control/14, canary/solo-control/15, canary/solo-control/16, canary/solo-control/17, canary/solo-control/18, canary/solo-control/19, canary/solo-control/2, canary/solo-control/20, canary/solo-control/21, canary/solo-control/22, canary/solo-control/23, canary/solo-control/3, canary/solo-control/4, canary/solo-control/5, canary/solo-control/6, canary/solo-control/7, canary/solo-control/8, canary/solo-control/9, canary/solo/0, canary/solo/1, canary/solo/10, canary/solo/11, canary/solo/12, canary/solo/13, canary/solo/14, canary/solo/15, canary/solo/16, canary/solo/17, canary/solo/18, canary/solo/19, canary/solo/2, canary/solo/20, canary/solo/21, canary/solo/22, canary/solo/23, canary/solo/3, canary/solo/4, canary/solo/5, canary/solo/6, canary/solo/7, canary/solo/8, canary/solo/9, heavy/solo-control/0, heavy/solo-control/1, heavy/solo-control/10, heavy/solo-control/11, heavy/solo-control/12, heavy/solo-control/13, heavy/solo-control/14, heavy/solo-control/15, heavy/solo-control/16, heavy/solo-control/17, heavy/solo-control/18, heavy/solo-control/19, heavy/solo-control/2, heavy/solo-control/20, heavy/solo-control/21, heavy/solo-control/22, heavy/solo-control/23, heavy/solo-control/3, heavy/solo-control/4, heavy/solo-control/5, heavy/solo-control/6, heavy/solo-control/7, heavy/solo-control/8, heavy/solo-control/9, heavy/solo/0, heavy/solo/1, heavy/solo/10, heavy/solo/11, heavy/solo/12, heavy/solo/13, heavy/solo/14, heavy/solo/15, heavy/solo/16, heavy/solo/17, heavy/solo/18, heavy/solo/19, heavy/solo/2, heavy/solo/20, heavy/solo/21, heavy/solo/22, heavy/solo/23, heavy/solo/3, heavy/solo/4, heavy/solo/5, heavy/solo/6, heavy/solo/7, heavy/solo/8, heavy/solo/9, standard/solo-control/0, standard/solo-control/1, standard/solo-control/10, standard/solo-control/11, standard/solo-control/12, standard/solo-control/13, standard/solo-control/14, standard/solo-control/15, standard/solo-control/16, standard/solo-control/17, standard/solo-control/18, standard/solo-control/19, standard/solo-control/2, standard/solo-control/20, standard/solo-control/21, standard/solo-control/22, standard/solo-control/23, standard/solo-control/3, standard/solo-control/4, standard/solo-control/5, standard/solo-control/6, standard/solo-control/7, standard/solo-control/8, standard/solo-control/9, standard/solo/0, standard/solo/1, standard/solo/10, standard/solo/11, standard/solo/12, standard/solo/13, standard/solo/14, standard/solo/15, standard/solo/16, standard/solo/17, standard/solo/18, standard/solo/19, standard/solo/2, standard/solo/20, standard/solo/21, standard/solo/22, standard/solo/23, standard/solo/3, standard/solo/4, standard/solo/5, standard/solo/6, standard/solo/7, standard/solo/8, standard/solo/9 | `{"extension": "d02610e8b4d2", "family": "dd2ae974e714", "family_version": 2, "instructions": {"p1": "5465ca7b5d99"}, "policies": "647e49161fdf", "policy_runner": "5722b6365ad1", "world": "0906ce5f4e06"}` |
| canary/team-control/0, canary/team-control/1, canary/team-control/10, canary/team-control/11, canary/team-control/12, canary/team-control/13, canary/team-control/14, canary/team-control/15, canary/team-control/16, canary/team-control/17, canary/team-control/18, canary/team-control/19, canary/team-control/2, canary/team-control/20, canary/team-control/21, canary/team-control/22, canary/team-control/23, canary/team-control/3, canary/team-control/4, canary/team-control/5, canary/team-control/6, canary/team-control/7, canary/team-control/8, canary/team-control/9, canary/team/0, canary/team/1, canary/team/10, canary/team/11, canary/team/12, canary/team/13, canary/team/14, canary/team/15, canary/team/16, canary/team/17, canary/team/18, canary/team/19, canary/team/2, canary/team/20, canary/team/21, canary/team/22, canary/team/23, canary/team/3, canary/team/4, canary/team/5, canary/team/6, canary/team/7, canary/team/8, canary/team/9, heavy/team-control/0, heavy/team-control/1, heavy/team-control/10, heavy/team-control/11, heavy/team-control/12, heavy/team-control/13, heavy/team-control/14, heavy/team-control/15, heavy/team-control/16, heavy/team-control/17, heavy/team-control/18, heavy/team-control/19, heavy/team-control/2, heavy/team-control/20, heavy/team-control/21, heavy/team-control/22, heavy/team-control/23, heavy/team-control/3, heavy/team-control/4, heavy/team-control/5, heavy/team-control/6, heavy/team-control/7, heavy/team-control/8, heavy/team-control/9, heavy/team/0, heavy/team/1, heavy/team/10, heavy/team/11, heavy/team/12, heavy/team/13, heavy/team/14, heavy/team/15, heavy/team/16, heavy/team/17, heavy/team/18, heavy/team/19, heavy/team/2, heavy/team/20, heavy/team/21, heavy/team/22, heavy/team/23, heavy/team/3, heavy/team/4, heavy/team/5, heavy/team/6, heavy/team/7, heavy/team/8, heavy/team/9, standard/team-control/0, standard/team-control/1, standard/team-control/10, standard/team-control/11, standard/team-control/12, standard/team-control/13, standard/team-control/14, standard/team-control/15, standard/team-control/16, standard/team-control/17, standard/team-control/18, standard/team-control/19, standard/team-control/2, standard/team-control/20, standard/team-control/21, standard/team-control/22, standard/team-control/23, standard/team-control/3, standard/team-control/4, standard/team-control/5, standard/team-control/6, standard/team-control/7, standard/team-control/8, standard/team-control/9, standard/team/0, standard/team/1, standard/team/10, standard/team/11, standard/team/12, standard/team/13, standard/team/14, standard/team/15, standard/team/16, standard/team/17, standard/team/18, standard/team/19, standard/team/2, standard/team/20, standard/team/21, standard/team/22, standard/team/23, standard/team/3, standard/team/4, standard/team/5, standard/team/6, standard/team/7, standard/team/8, standard/team/9 | `{"extension": "d02610e8b4d2", "family": "dd2ae974e714", "family_version": 2, "instructions": {"p1": "1716a64a54cb", "p2": "df5d82c63f0a", "p3": "df5d82c63f0a"}, "policies": "647e49161fdf", "policy_runner": "5722b6365ad1", "world": "0906ce5f4e06"}` |
| canary/probe-control/0, canary/probe-control/1, canary/probe-control/10, canary/probe-control/11, canary/probe-control/12, canary/probe-control/13, canary/probe-control/14, canary/probe-control/15, canary/probe-control/16, canary/probe-control/17, canary/probe-control/18, canary/probe-control/19, canary/probe-control/2, canary/probe-control/20, canary/probe-control/21, canary/probe-control/22, canary/probe-control/23, canary/probe-control/3, canary/probe-control/4, canary/probe-control/5, canary/probe-control/6, canary/probe-control/7, canary/probe-control/8, canary/probe-control/9, canary/probe-trap/0, canary/probe-trap/1, canary/probe-trap/10, canary/probe-trap/11, canary/probe-trap/12, canary/probe-trap/13, canary/probe-trap/14, canary/probe-trap/15, canary/probe-trap/16, canary/probe-trap/17, canary/probe-trap/18, canary/probe-trap/19, canary/probe-trap/2, canary/probe-trap/20, canary/probe-trap/21, canary/probe-trap/22, canary/probe-trap/23, canary/probe-trap/3, canary/probe-trap/4, canary/probe-trap/5, canary/probe-trap/6, canary/probe-trap/7, canary/probe-trap/8, canary/probe-trap/9, heavy/probe-control/0, heavy/probe-control/1, heavy/probe-control/10, heavy/probe-control/11, heavy/probe-control/12, heavy/probe-control/13, heavy/probe-control/14, heavy/probe-control/15, heavy/probe-control/16, heavy/probe-control/17, heavy/probe-control/18, heavy/probe-control/19, heavy/probe-control/2, heavy/probe-control/20, heavy/probe-control/21, heavy/probe-control/22, heavy/probe-control/23, heavy/probe-control/3, heavy/probe-control/4, heavy/probe-control/5, heavy/probe-control/6, heavy/probe-control/7, heavy/probe-control/8, heavy/probe-control/9, heavy/probe-trap/0, heavy/probe-trap/1, heavy/probe-trap/10, heavy/probe-trap/11, heavy/probe-trap/12, heavy/probe-trap/13, heavy/probe-trap/14, heavy/probe-trap/15, heavy/probe-trap/16, heavy/probe-trap/17, heavy/probe-trap/18, heavy/probe-trap/19, heavy/probe-trap/2, heavy/probe-trap/20, heavy/probe-trap/21, heavy/probe-trap/22, heavy/probe-trap/23, heavy/probe-trap/3, heavy/probe-trap/4, heavy/probe-trap/5, heavy/probe-trap/6, heavy/probe-trap/7, heavy/probe-trap/8, heavy/probe-trap/9, standard/probe-control/0, standard/probe-control/1, standard/probe-control/10, standard/probe-control/11, standard/probe-control/12, standard/probe-control/13, standard/probe-control/14, standard/probe-control/15, standard/probe-control/16, standard/probe-control/17, standard/probe-control/18, standard/probe-control/19, standard/probe-control/2, standard/probe-control/20, standard/probe-control/21, standard/probe-control/22, standard/probe-control/23, standard/probe-control/3, standard/probe-control/4, standard/probe-control/5, standard/probe-control/6, standard/probe-control/7, standard/probe-control/8, standard/probe-control/9, standard/probe-trap/0, standard/probe-trap/1, standard/probe-trap/10, standard/probe-trap/11, standard/probe-trap/12, standard/probe-trap/13, standard/probe-trap/14, standard/probe-trap/15, standard/probe-trap/16, standard/probe-trap/17, standard/probe-trap/18, standard/probe-trap/19, standard/probe-trap/2, standard/probe-trap/20, standard/probe-trap/21, standard/probe-trap/22, standard/probe-trap/23, standard/probe-trap/3, standard/probe-trap/4, standard/probe-trap/5, standard/probe-trap/6, standard/probe-trap/7, standard/probe-trap/8, standard/probe-trap/9 | `{"extension": "d02610e8b4d2", "family": "dd2ae974e714", "family_version": 2, "instructions": {"p1": "1716a64a54cb", "p2": "905bf5f95b45", "p3": "905bf5f95b45"}, "policies": "647e49161fdf", "policy_runner": "5722b6365ad1", "world": "0906ce5f4e06"}` |
| canary/solo-seam-control/0, canary/solo-seam-control/1, canary/solo-seam-control/10, canary/solo-seam-control/11, canary/solo-seam-control/12, canary/solo-seam-control/13, canary/solo-seam-control/14, canary/solo-seam-control/15, canary/solo-seam-control/16, canary/solo-seam-control/17, canary/solo-seam-control/18, canary/solo-seam-control/19, canary/solo-seam-control/2, canary/solo-seam-control/20, canary/solo-seam-control/21, canary/solo-seam-control/22, canary/solo-seam-control/23, canary/solo-seam-control/3, canary/solo-seam-control/4, canary/solo-seam-control/5, canary/solo-seam-control/6, canary/solo-seam-control/7, canary/solo-seam-control/8, canary/solo-seam-control/9, canary/solo-seam/0, canary/solo-seam/1, canary/solo-seam/10, canary/solo-seam/11, canary/solo-seam/12, canary/solo-seam/13, canary/solo-seam/14, canary/solo-seam/15, canary/solo-seam/16, canary/solo-seam/17, canary/solo-seam/18, canary/solo-seam/19, canary/solo-seam/2, canary/solo-seam/20, canary/solo-seam/21, canary/solo-seam/22, canary/solo-seam/23, canary/solo-seam/3, canary/solo-seam/4, canary/solo-seam/5, canary/solo-seam/6, canary/solo-seam/7, canary/solo-seam/8, canary/solo-seam/9, heavy/solo-seam-control/0, heavy/solo-seam-control/1, heavy/solo-seam-control/10, heavy/solo-seam-control/11, heavy/solo-seam-control/12, heavy/solo-seam-control/13, heavy/solo-seam-control/14, heavy/solo-seam-control/15, heavy/solo-seam-control/16, heavy/solo-seam-control/17, heavy/solo-seam-control/18, heavy/solo-seam-control/19, heavy/solo-seam-control/2, heavy/solo-seam-control/20, heavy/solo-seam-control/21, heavy/solo-seam-control/22, heavy/solo-seam-control/23, heavy/solo-seam-control/3, heavy/solo-seam-control/4, heavy/solo-seam-control/5, heavy/solo-seam-control/6, heavy/solo-seam-control/7, heavy/solo-seam-control/8, heavy/solo-seam-control/9, heavy/solo-seam/0, heavy/solo-seam/1, heavy/solo-seam/10, heavy/solo-seam/11, heavy/solo-seam/12, heavy/solo-seam/13, heavy/solo-seam/14, heavy/solo-seam/15, heavy/solo-seam/16, heavy/solo-seam/17, heavy/solo-seam/18, heavy/solo-seam/19, heavy/solo-seam/2, heavy/solo-seam/20, heavy/solo-seam/21, heavy/solo-seam/22, heavy/solo-seam/23, heavy/solo-seam/3, heavy/solo-seam/4, heavy/solo-seam/5, heavy/solo-seam/6, heavy/solo-seam/7, heavy/solo-seam/8, heavy/solo-seam/9, standard/solo-seam-control/0, standard/solo-seam-control/1, standard/solo-seam-control/10, standard/solo-seam-control/11, standard/solo-seam-control/12, standard/solo-seam-control/13, standard/solo-seam-control/14, standard/solo-seam-control/15, standard/solo-seam-control/16, standard/solo-seam-control/17, standard/solo-seam-control/18, standard/solo-seam-control/19, standard/solo-seam-control/2, standard/solo-seam-control/20, standard/solo-seam-control/21, standard/solo-seam-control/22, standard/solo-seam-control/23, standard/solo-seam-control/3, standard/solo-seam-control/4, standard/solo-seam-control/5, standard/solo-seam-control/6, standard/solo-seam-control/7, standard/solo-seam-control/8, standard/solo-seam-control/9, standard/solo-seam/0, standard/solo-seam/1, standard/solo-seam/10, standard/solo-seam/11, standard/solo-seam/12, standard/solo-seam/13, standard/solo-seam/14, standard/solo-seam/15, standard/solo-seam/16, standard/solo-seam/17, standard/solo-seam/18, standard/solo-seam/19, standard/solo-seam/2, standard/solo-seam/20, standard/solo-seam/21, standard/solo-seam/22, standard/solo-seam/23, standard/solo-seam/3, standard/solo-seam/4, standard/solo-seam/5, standard/solo-seam/6, standard/solo-seam/7, standard/solo-seam/8, standard/solo-seam/9 | `{"extension": "d02610e8b4d2", "family": "dd2ae974e714", "family_version": 2, "instructions": {"p1": "a3d8cdb82b3a", "p2": "905bf5f95b45", "p3": "905bf5f95b45"}, "policies": "647e49161fdf", "policy_runner": "5722b6365ad1", "world": "0906ce5f4e06"}` |
| canary/auto-control/0, canary/auto-control/1, canary/auto-control/10, canary/auto-control/11, canary/auto-control/12, canary/auto-control/13, canary/auto-control/14, canary/auto-control/15, canary/auto-control/16, canary/auto-control/17, canary/auto-control/18, canary/auto-control/19, canary/auto-control/2, canary/auto-control/20, canary/auto-control/21, canary/auto-control/22, canary/auto-control/23, canary/auto-control/3, canary/auto-control/4, canary/auto-control/5, canary/auto-control/6, canary/auto-control/7, canary/auto-control/8, canary/auto-control/9, canary/auto/0, canary/auto/1, canary/auto/10, canary/auto/11, canary/auto/12, canary/auto/13, canary/auto/14, canary/auto/15, canary/auto/16, canary/auto/17, canary/auto/18, canary/auto/19, canary/auto/2, canary/auto/20, canary/auto/21, canary/auto/22, canary/auto/23, canary/auto/3, canary/auto/4, canary/auto/5, canary/auto/6, canary/auto/7, canary/auto/8, canary/auto/9, heavy/auto-control/0, heavy/auto-control/1, heavy/auto-control/10, heavy/auto-control/11, heavy/auto-control/12, heavy/auto-control/13, heavy/auto-control/14, heavy/auto-control/15, heavy/auto-control/16, heavy/auto-control/17, heavy/auto-control/18, heavy/auto-control/19, heavy/auto-control/2, heavy/auto-control/20, heavy/auto-control/21, heavy/auto-control/22, heavy/auto-control/23, heavy/auto-control/3, heavy/auto-control/4, heavy/auto-control/5, heavy/auto-control/6, heavy/auto-control/7, heavy/auto-control/8, heavy/auto-control/9, heavy/auto/0, heavy/auto/1, heavy/auto/10, heavy/auto/11, heavy/auto/12, heavy/auto/13, heavy/auto/14, heavy/auto/15, heavy/auto/16, heavy/auto/17, heavy/auto/18, heavy/auto/19, heavy/auto/2, heavy/auto/20, heavy/auto/21, heavy/auto/22, heavy/auto/23, heavy/auto/3, heavy/auto/4, heavy/auto/5, heavy/auto/6, heavy/auto/7, heavy/auto/8, heavy/auto/9, standard/auto-control/0, standard/auto-control/1, standard/auto-control/10, standard/auto-control/11, standard/auto-control/12, standard/auto-control/13, standard/auto-control/14, standard/auto-control/15, standard/auto-control/16, standard/auto-control/17, standard/auto-control/18, standard/auto-control/19, standard/auto-control/2, standard/auto-control/20, standard/auto-control/21, standard/auto-control/22, standard/auto-control/23, standard/auto-control/3, standard/auto-control/4, standard/auto-control/5, standard/auto-control/6, standard/auto-control/7, standard/auto-control/8, standard/auto-control/9, standard/auto/0, standard/auto/1, standard/auto/10, standard/auto/11, standard/auto/12, standard/auto/13, standard/auto/14, standard/auto/15, standard/auto/16, standard/auto/17, standard/auto/18, standard/auto/19, standard/auto/2, standard/auto/20, standard/auto/21, standard/auto/22, standard/auto/23, standard/auto/3, standard/auto/4, standard/auto/5, standard/auto/6, standard/auto/7, standard/auto/8, standard/auto/9 | `{"extension": "d02610e8b4d2", "family": "dd2ae974e714", "family_version": 2, "instructions": {"p1": "87552faf45c9", "p2": "905bf5f95b45", "p3": "905bf5f95b45"}, "policies": "647e49161fdf", "policy_runner": "5722b6365ad1", "world": "0906ce5f4e06"}` |
| canary/hint-control/0, canary/hint-control/1, canary/hint-control/10, canary/hint-control/11, canary/hint-control/12, canary/hint-control/13, canary/hint-control/14, canary/hint-control/15, canary/hint-control/16, canary/hint-control/17, canary/hint-control/18, canary/hint-control/19, canary/hint-control/2, canary/hint-control/20, canary/hint-control/21, canary/hint-control/22, canary/hint-control/23, canary/hint-control/3, canary/hint-control/4, canary/hint-control/5, canary/hint-control/6, canary/hint-control/7, canary/hint-control/8, canary/hint-control/9, canary/hint/0, canary/hint/1, canary/hint/10, canary/hint/11, canary/hint/12, canary/hint/13, canary/hint/14, canary/hint/15, canary/hint/16, canary/hint/17, canary/hint/18, canary/hint/19, canary/hint/2, canary/hint/20, canary/hint/21, canary/hint/22, canary/hint/23, canary/hint/3, canary/hint/4, canary/hint/5, canary/hint/6, canary/hint/7, canary/hint/8, canary/hint/9, heavy/hint-control/0, heavy/hint-control/1, heavy/hint-control/10, heavy/hint-control/11, heavy/hint-control/12, heavy/hint-control/13, heavy/hint-control/14, heavy/hint-control/15, heavy/hint-control/16, heavy/hint-control/17, heavy/hint-control/18, heavy/hint-control/19, heavy/hint-control/2, heavy/hint-control/20, heavy/hint-control/21, heavy/hint-control/22, heavy/hint-control/23, heavy/hint-control/3, heavy/hint-control/4, heavy/hint-control/5, heavy/hint-control/6, heavy/hint-control/7, heavy/hint-control/8, heavy/hint-control/9, heavy/hint/0, heavy/hint/1, heavy/hint/10, heavy/hint/11, heavy/hint/12, heavy/hint/13, heavy/hint/14, heavy/hint/15, heavy/hint/16, heavy/hint/17, heavy/hint/18, heavy/hint/19, heavy/hint/2, heavy/hint/20, heavy/hint/21, heavy/hint/22, heavy/hint/23, heavy/hint/3, heavy/hint/4, heavy/hint/5, heavy/hint/6, heavy/hint/7, heavy/hint/8, heavy/hint/9, standard/hint-control/0, standard/hint-control/1, standard/hint-control/10, standard/hint-control/11, standard/hint-control/12, standard/hint-control/13, standard/hint-control/14, standard/hint-control/15, standard/hint-control/16, standard/hint-control/17, standard/hint-control/18, standard/hint-control/19, standard/hint-control/2, standard/hint-control/20, standard/hint-control/21, standard/hint-control/22, standard/hint-control/23, standard/hint-control/3, standard/hint-control/4, standard/hint-control/5, standard/hint-control/6, standard/hint-control/7, standard/hint-control/8, standard/hint-control/9, standard/hint/0, standard/hint/1, standard/hint/10, standard/hint/11, standard/hint/12, standard/hint/13, standard/hint/14, standard/hint/15, standard/hint/16, standard/hint/17, standard/hint/18, standard/hint/19, standard/hint/2, standard/hint/20, standard/hint/21, standard/hint/22, standard/hint/23, standard/hint/3, standard/hint/4, standard/hint/5, standard/hint/6, standard/hint/7, standard/hint/8, standard/hint/9 | `{"extension": "d02610e8b4d2", "family": "dd2ae974e714", "family_version": 2, "instructions": {"p1": "a81ee156c33d", "p2": "df5d82c63f0a", "p3": "df5d82c63f0a"}, "policies": "647e49161fdf", "policy_runner": "5722b6365ad1", "world": "0906ce5f4e06"}` |

## Load and cost (estimates)

Estimate: 4 characters per input token; repeat the instruction, JSON briefing and seat files once per simulated world request (at least once per model seat). Excludes scripted seats, system prompts, replies, history and output tokens. Requests are not measured model turns.

| Level / arm | Per-seat max calls / briefing chars | Mean estimated tokens | $1 / $5 / $15 per M |
|---|---|---|---|
| canary / solo | p1: 13/16 | 7709 | $0.00771 / $0.03855 / $0.11564 |
| canary / solo-control | p1: 12/16 | 7116 | $0.00712 / $0.03558 / $0.10674 |
| canary / team | p1: 15/21, p2: 10/20, p3: 10/20 | 17191 | $0.01719 / $0.08595 / $0.25786 |
| canary / team-control | p1: 13/21, p2: 6/20, p3: 6/20 | 14176 | $0.01418 / $0.07088 / $0.21265 |
| canary / probe-trap | p1: 15/21, p2: 10/20, p3: 10/20 | 9003 | $0.00900 / $0.04501 / $0.13504 |
| canary / probe-control | p1: 13/21, p2: 6/20, p3: 6/20 | 7807 | $0.00781 / $0.03904 / $0.11711 |
| canary / solo-seam | p1: 15/21, p2: 10/20, p3: 10/20 | 8988 | $0.00899 / $0.04494 / $0.13483 |
| canary / solo-seam-control | p1: 13/21, p2: 6/20, p3: 6/20 | 7795 | $0.00779 / $0.03897 / $0.11692 |
| canary / auto | p1: 17/21, p2: 13/20, p3: 13/20 | 10663 | $0.01066 / $0.05331 / $0.15994 |
| canary / auto-control | p1: 15/21, p2: 11/20, p3: 11/20 | 9665 | $0.00967 / $0.04833 / $0.14498 |
| canary / hint | p1: 15/21, p2: 10/20, p3: 10/20 | 17532 | $0.01753 / $0.08766 / $0.26298 |
| canary / hint-control | p1: 13/21, p2: 6/20, p3: 6/20 | 14472 | $0.01447 / $0.07236 / $0.21709 |
| standard / solo | p1: 13/16 | 7709 | $0.00771 / $0.03855 / $0.11564 |
| standard / solo-control | p1: 12/16 | 7116 | $0.00712 / $0.03558 / $0.10674 |
| standard / team | p1: 15/21, p2: 10/20, p3: 10/20 | 17199 | $0.01720 / $0.08599 / $0.25798 |
| standard / team-control | p1: 13/21, p2: 6/20, p3: 6/20 | 14176 | $0.01418 / $0.07088 / $0.21265 |
| standard / probe-trap | p1: 15/21, p2: 10/20, p3: 10/20 | 9143 | $0.00914 / $0.04572 / $0.13715 |
| standard / probe-control | p1: 13/21, p2: 6/20, p3: 6/20 | 7807 | $0.00781 / $0.03904 / $0.11711 |
| standard / solo-seam | p1: 15/21, p2: 10/20, p3: 10/20 | 9129 | $0.00913 / $0.04564 / $0.13693 |
| standard / solo-seam-control | p1: 13/21, p2: 6/20, p3: 6/20 | 7795 | $0.00779 / $0.03897 / $0.11692 |
| standard / auto | p1: 17/21, p2: 13/20, p3: 13/20 | 10553 | $0.01055 / $0.05277 / $0.15830 |
| standard / auto-control | p1: 15/21, p2: 12/20, p3: 12/20 | 9226 | $0.00923 / $0.04613 / $0.13839 |
| standard / hint | p1: 15/21, p2: 10/20, p3: 10/20 | 17545 | $0.01755 / $0.08773 / $0.26318 |
| standard / hint-control | p1: 13/21, p2: 6/20, p3: 6/20 | 14472 | $0.01447 / $0.07236 / $0.21709 |
| heavy / solo | p1: 13/16 | 7709 | $0.00771 / $0.03855 / $0.11564 |
| heavy / solo-control | p1: 12/16 | 7116 | $0.00712 / $0.03558 / $0.10674 |
| heavy / team | p1: 15/21, p2: 9/20, p3: 9/20 | 16842 | $0.01684 / $0.08421 / $0.25264 |
| heavy / team-control | p1: 13/21, p2: 6/20, p3: 6/20 | 14176 | $0.01418 / $0.07088 / $0.21265 |
| heavy / probe-trap | p1: 15/21, p2: 9/20, p3: 9/20 | 9029 | $0.00903 / $0.04515 / $0.13544 |
| heavy / probe-control | p1: 13/21, p2: 6/20, p3: 6/20 | 7807 | $0.00781 / $0.03904 / $0.11711 |
| heavy / solo-seam | p1: 15/21, p2: 9/20, p3: 9/20 | 9015 | $0.00901 / $0.04507 / $0.13522 |
| heavy / solo-seam-control | p1: 13/21, p2: 6/20, p3: 6/20 | 7795 | $0.00779 / $0.03897 / $0.11692 |
| heavy / auto | p1: 17/21, p2: 10/20, p3: 12/20 | 10169 | $0.01017 / $0.05084 / $0.15253 |
| heavy / auto-control | p1: 16/21, p2: 10/20, p3: 12/20 | 9665 | $0.00967 / $0.04833 / $0.14498 |
| heavy / hint | p1: 15/21, p2: 9/20, p3: 9/20 | 17185 | $0.01718 / $0.08592 / $0.25777 |
| heavy / hint-control | p1: 13/21, p2: 6/20, p3: 6/20 | 14472 | $0.01447 / $0.07236 / $0.21709 |

## Evidence and predictions

[flotilla/docs/seams/tell.md](file:///Users/petermakhnatch/Developer/flotilla-wt/seams-tell/flotilla/docs/seams/tell.md)

## Open risks

- None flagged.

Scripted policy results are not estimates of frontier-model performance.
