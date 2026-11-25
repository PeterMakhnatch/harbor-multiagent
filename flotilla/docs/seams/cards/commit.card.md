# EvalCard: commit — Book the day's outbound freight

**Seam:** Apply an outage update received during an ongoing batch before committing a final booking.
**Kit commit:** `e9be54cbdc188db1a68e0044389765947095c6c5`; variant: `default`.
**Arms:** solo, solo-seam, team, probe-control, probe-trap, hint, probe-quiet. Levels: canary (canary), standard (standard), heavy (heavy).
**Sweep:** 24 instances × 3 schedule seeds. No model calls.
**Policies unavailable:** none.

## Policy matrix (mean / min / n; invalid count in parentheses)

| Level | Arm | Seats | careful | naive | nop | blind | random | lone | lapse-10 | lapse-20 | lapse-30 | lapse-50 | script-loop |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| canary | solo | p1 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| canary | solo-seam | p1 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.917 / 0.000 / 72 (0) | 0.917 / 0.000 / 72 (0) | 0.458 / 0.000 / 72 (0) | 0.708 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) |
| canary | team | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.792 / 0.000 / 72 (0) | 0.750 / 0.000 / 72 (0) | 0.292 / 0.000 / 72 (0) | 0.333 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) |
| canary | probe-control | p1, p2 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| canary | probe-trap | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.917 / 0.000 / 72 (0) | 0.917 / 0.000 / 72 (0) | 0.458 / 0.000 / 72 (0) | 0.708 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) |
| canary | hint | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.917 / 0.000 / 72 (0) | 0.917 / 0.000 / 72 (0) | 0.458 / 0.000 / 72 (0) | 0.708 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) |
| canary | probe-quiet | p1, p2 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard | solo | p1 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard | solo-seam | p1 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.833 / 0.000 / 72 (0) | 0.583 / 0.000 / 72 (0) | 0.458 / 0.000 / 72 (0) | 0.250 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) |
| standard | team | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.750 / 0.000 / 72 (0) | 0.167 / 0.000 / 72 (0) | 0.167 / 0.000 / 72 (0) | 0.083 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) |
| standard | probe-control | p1, p2 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard | probe-trap | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.833 / 0.000 / 72 (0) | 0.583 / 0.000 / 72 (0) | 0.458 / 0.000 / 72 (0) | 0.250 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) |
| standard | hint | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.833 / 0.000 / 72 (0) | 0.583 / 0.000 / 72 (0) | 0.458 / 0.000 / 72 (0) | 0.250 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) |
| standard | probe-quiet | p1, p2 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy | solo | p1 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy | solo-seam | p1 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.750 / 0.000 / 72 (0) | 0.625 / 0.000 / 72 (0) | 0.333 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) |
| heavy | team | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.500 / 0.000 / 72 (0) | 0.333 / 0.000 / 72 (0) | 0.208 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) |
| heavy | probe-control | p1, p2 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy | probe-trap | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.750 / 0.000 / 72 (0) | 0.625 / 0.000 / 72 (0) | 0.333 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) |
| heavy | hint | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.750 / 0.000 / 72 (0) | 0.625 / 0.000 / 72 (0) | 0.333 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) |
| heavy | probe-quiet | p1, p2 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |

## Checks

| Check | Standard | Result | Evidence |
|---|---|---|---|
| levels | A5 | **PASS** | All requested dose levels available |
| solvable | A2/B1 | **PASS** | 1512 runs; careful reward = 1 |
| easy alone | A2 | **PASS** | 216 runs; solo careful reward = 1 |
| floor | A3 | **PASS** | 1512 runs; every nop reward ≤ 0.05 |
| chance | A3 | **PASS** | canary/team/blind: actual 0.0, expected 0.0500 (absolute tolerance 0.1); canary/team/random: actual 0.0, expected 0.0000 (absolute tolerance 0.1); canary/probe-trap/blind: actual 0.0, expected 0.0500 (absolute tolerance 0.1); canary/probe-trap/random: actual 0.0, expected 0.0000 (absolute tolerance 0.1); standard/team/blind: actual 0.0, expected 0.0067 (absolute tolerance 0.1); standard/team/ra… (full evidence in JSON) |
| separation | A4 | **PASS** | canary/probe-trap: gap 1.000; canary/probe-control: naive min 1.0; standard/probe-trap: gap 1.000; standard/probe-control: naive min 1.0; heavy/probe-trap: gap 1.000; heavy/probe-control: naive min 1.0 |
| dose response | A5/I1 | **PASS** | canary → standard → heavy: [0.0, 0.0, 0.0] |
| one seat is not enough | A3/C1 | **PASS** | 216 runs; every lone team reward ≤ 0.5 |
| schedule independence | E3 | **PASS** | 1512 runs; careful scores 1 across 3 schedule seed(s); max within-instance naive spread: 0.0 |
| simulation validity | F1 | **PASS** | 16632 runs; No invalid runs in the ordinary sweep |
| determinism | E1/E2 | **PASS** | 42/42 sample event hashes match |
| no leak | C1/D2 | **PASS** | 504 built tasks; seat views and family QA scanned |
| pairs | A1/D2 | **PASS** | probe-control→probe-quiet: 72 pairs, max 0 changed chars; fields: none; probe-control→probe-trap: 72 pairs, max 0 changed chars; fields: none; probe-trap→hint: 72 pairs, max 92 changed chars; fields: p1/instruction; probe-trap→solo-seam: 72 pairs, max 9 changed chars; fields: p1/instruction; probe-trap→team: 72 pairs, max 1870 changed chars; fields: p2/env.SEAT, p2/env.SEAT_BRIEFING, p2/env.WOR… (full evidence in JSON) |
| twin rule | A1 | **PASS** | Every team/trap has a declared twin |
| invalid is not zero | F1 | **PASS** | Budget-limited simulation: stalled=True, invalid=True, reward=None |
| canary | A6 | **PASS** | Marker checked in 504 task files and all seat views |
| power plan | A3 | **PASS** | 30 paired comparisons; fixed exact McNemar plan (see table) |

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
| canary | blind | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| canary | blind | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | blind | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | random | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| canary | random | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | random | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | lone | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| canary | lone | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | lone | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-10 | boundary | solo − team | +0.208 | 72 / 24 | 0 |
| canary | lapse-10 | teammate | solo-seam − team | +0.125 | 72 / 24 | 0 |
| canary | lapse-10 | protocol | hint − team | +0.125 | 72 / 24 | 0 |
| canary | lapse-20 | boundary | solo − team | +0.250 | 72 / 24 | 0 |
| canary | lapse-20 | teammate | solo-seam − team | +0.167 | 72 / 24 | 0 |
| canary | lapse-20 | protocol | hint − team | +0.167 | 72 / 24 | 0 |
| canary | lapse-30 | boundary | solo − team | +0.708 | 72 / 24 | 0 |
| canary | lapse-30 | teammate | solo-seam − team | +0.167 | 72 / 24 | 0 |
| canary | lapse-30 | protocol | hint − team | +0.167 | 72 / 24 | 0 |
| canary | lapse-50 | boundary | solo − team | +0.667 | 72 / 24 | 0 |
| canary | lapse-50 | teammate | solo-seam − team | +0.375 | 72 / 24 | 0 |
| canary | lapse-50 | protocol | hint − team | +0.375 | 72 / 24 | 0 |
| canary | script-loop | boundary | solo − team | +1.000 | 72 / 24 | 0 |
| canary | script-loop | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | script-loop | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | careful | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| standard | careful | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | careful | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | naive | boundary | solo − team | +1.000 | 72 / 24 | 0 |
| standard | naive | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | naive | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | nop | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| standard | nop | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | nop | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | blind | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| standard | blind | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | blind | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | random | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| standard | random | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | random | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | lone | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| standard | lone | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | lone | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-10 | boundary | solo − team | +0.250 | 72 / 24 | 0 |
| standard | lapse-10 | teammate | solo-seam − team | +0.083 | 72 / 24 | 0 |
| standard | lapse-10 | protocol | hint − team | +0.083 | 72 / 24 | 0 |
| standard | lapse-20 | boundary | solo − team | +0.833 | 72 / 24 | 0 |
| standard | lapse-20 | teammate | solo-seam − team | +0.417 | 72 / 24 | 0 |
| standard | lapse-20 | protocol | hint − team | +0.417 | 72 / 24 | 0 |
| standard | lapse-30 | boundary | solo − team | +0.833 | 72 / 24 | 0 |
| standard | lapse-30 | teammate | solo-seam − team | +0.292 | 72 / 24 | 0 |
| standard | lapse-30 | protocol | hint − team | +0.292 | 72 / 24 | 0 |
| standard | lapse-50 | boundary | solo − team | +0.917 | 72 / 24 | 0 |
| standard | lapse-50 | teammate | solo-seam − team | +0.167 | 72 / 24 | 0 |
| standard | lapse-50 | protocol | hint − team | +0.167 | 72 / 24 | 0 |
| standard | script-loop | boundary | solo − team | +1.000 | 72 / 24 | 0 |
| standard | script-loop | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | script-loop | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | careful | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| heavy | careful | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | careful | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | naive | boundary | solo − team | +1.000 | 72 / 24 | 0 |
| heavy | naive | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | naive | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | nop | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| heavy | nop | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | nop | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | blind | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| heavy | blind | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | blind | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | random | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| heavy | random | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | random | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | lone | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| heavy | lone | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | lone | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | lapse-10 | boundary | solo − team | +0.500 | 72 / 24 | 0 |
| heavy | lapse-10 | teammate | solo-seam − team | +0.250 | 72 / 24 | 0 |
| heavy | lapse-10 | protocol | hint − team | +0.250 | 72 / 24 | 0 |
| heavy | lapse-20 | boundary | solo − team | +0.667 | 72 / 24 | 0 |
| heavy | lapse-20 | teammate | solo-seam − team | +0.292 | 72 / 24 | 0 |
| heavy | lapse-20 | protocol | hint − team | +0.292 | 72 / 24 | 0 |
| heavy | lapse-30 | boundary | solo − team | +0.792 | 72 / 24 | 0 |
| heavy | lapse-30 | teammate | solo-seam − team | +0.125 | 72 / 24 | 0 |
| heavy | lapse-30 | protocol | hint − team | +0.125 | 72 / 24 | 0 |
| heavy | lapse-50 | boundary | solo − team | +1.000 | 72 / 24 | 0 |
| heavy | lapse-50 | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | lapse-50 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | script-loop | boundary | solo − team | +1.000 | 72 / 24 | 0 |
| heavy | script-loop | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | script-loop | protocol | hint − team | +0.000 | 72 / 24 | 0 |

## Power plan (scripted stand-ins)

Fixed sample, exact two-sided McNemar, α=0.05, target power 0.8; binary success means reward = 1. The paired reward effect also includes partial credit.
Schedule repeats estimate outcome probabilities; they are not independent instances. The planned n assumes independent new instances, one paired run each.
No-effect and NA cells have no planned n. Plans search up to 1,000 instances. These are sensitivity estimates, not model performance predictions.

| Level | Policy | Control − treatment | Reward effect | Success effect | + / − | Valid pairs / instances | Excluded | Planned n |
|---|---|---|---|---|---|---|---|---|
| canary | lapse-10 | probe-control − probe-trap | +0.083 | +0.083 | 6 / 0 | 72 / 24 | 0 | 94 |
| canary | lapse-10 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| canary | lapse-20 | probe-control − probe-trap | +0.083 | +0.083 | 6 / 0 | 72 / 24 | 0 | 94 |
| canary | lapse-20 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| canary | lapse-30 | probe-control − probe-trap | +0.542 | +0.542 | 39 / 0 | 72 / 24 | 0 | 13 |
| canary | lapse-30 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| canary | lapse-50 | probe-control − probe-trap | +0.292 | +0.292 | 21 / 0 | 72 / 24 | 0 | 26 |
| canary | lapse-50 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| canary | script-loop | probe-control − probe-trap | +1.000 | +1.000 | 72 / 0 | 72 / 24 | 0 | 6 |
| canary | script-loop | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| standard | lapse-10 | probe-control − probe-trap | +0.167 | +0.167 | 12 / 0 | 72 / 24 | 0 | 46 |
| standard | lapse-10 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| standard | lapse-20 | probe-control − probe-trap | +0.417 | +0.417 | 30 / 0 | 72 / 24 | 0 | 18 |
| standard | lapse-20 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| standard | lapse-30 | probe-control − probe-trap | +0.542 | +0.542 | 39 / 0 | 72 / 24 | 0 | 13 |
| standard | lapse-30 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| standard | lapse-50 | probe-control − probe-trap | +0.750 | +0.750 | 54 / 0 | 72 / 24 | 0 | 9 |
| standard | lapse-50 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| standard | script-loop | probe-control − probe-trap | +1.000 | +1.000 | 72 / 0 | 72 / 24 | 0 | 6 |
| standard | script-loop | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| heavy | lapse-10 | probe-control − probe-trap | +0.250 | +0.250 | 18 / 0 | 72 / 24 | 0 | 31 |
| heavy | lapse-10 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| heavy | lapse-20 | probe-control − probe-trap | +0.375 | +0.375 | 27 / 0 | 72 / 24 | 0 | 20 |
| heavy | lapse-20 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| heavy | lapse-30 | probe-control − probe-trap | +0.667 | +0.667 | 48 / 0 | 72 / 24 | 0 | 11 |
| heavy | lapse-30 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| heavy | lapse-50 | probe-control − probe-trap | +1.000 | +1.000 | 72 / 0 | 72 / 24 | 0 | 6 |
| heavy | lapse-50 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| heavy | script-loop | probe-control − probe-trap | +1.000 | +1.000 | 72 / 0 | 72 / 24 | 0 | 6 |
| heavy | script-loop | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |

## Versions

| Task(s) | Simulator versions |
|---|---|
| canary/solo/0 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "891f668d39ae"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo-seam/0 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "89e888476edb"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/team/0 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "2f8ca417d181", "p2": "f01ad066d0be"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/probe-control/0, canary/probe-quiet/0, canary/probe-trap/0 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "2f8ca417d181", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/hint/0 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "716783d28a45", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo/1 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "9e2f7d93197c"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo-seam/1 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "d492dc77b039"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/team/1 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "9843e574df51", "p2": "ec006b096be2"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/probe-control/1, canary/probe-quiet/1, canary/probe-trap/1 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "9843e574df51", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/hint/1 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "656d549b0782", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo/2 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "59ed9dc743d4"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo-seam/2 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "daf736df8044"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/team/2 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "0234badd4f4b", "p2": "514467956d38"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/probe-control/2, canary/probe-quiet/2, canary/probe-trap/2 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "0234badd4f4b", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/hint/2 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "0e3b4e33cf95", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo/3 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "fa3c34ea307a"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo-seam/3 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "18725a73adfb"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/team/3 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "0855a6556e8a", "p2": "85d47bdaac00"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/probe-control/3, canary/probe-quiet/3, canary/probe-trap/3 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "0855a6556e8a", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/hint/3 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "e74c6ecb7888", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo/4 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "601d08574f09"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo-seam/4 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "dc534a201779"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/team/4 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "40fad08528ce", "p2": "b04774c1f9c3"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/probe-control/4, canary/probe-quiet/4, canary/probe-trap/4 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "40fad08528ce", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/hint/4 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "e6eee3a1f488", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo/5 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "5751edb0a3b7"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo-seam/5 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "66351bf63327"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/team/5 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "8155d0c0df3f", "p2": "5abd7f4226a6"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/probe-control/5, canary/probe-quiet/5, canary/probe-trap/5 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "8155d0c0df3f", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/hint/5 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "9c1351718bb4", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo/6 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "1f38d5878ea3"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo-seam/6 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "ed70bb49c7ff"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/team/6 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "d076d60b7152", "p2": "509d76b4c341"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/probe-control/6, canary/probe-quiet/6, canary/probe-trap/6 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "d076d60b7152", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/hint/6 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "ec88efbbc16f", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo/7 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "bc9ac34357ca"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo-seam/7 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "2f8f984a0725"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/team/7 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "3e7d464491b0", "p2": "091537212c53"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/probe-control/7, canary/probe-quiet/7, canary/probe-trap/7 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "3e7d464491b0", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/hint/7 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "714f98221ad5", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo/8 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "14823049cf8d"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo-seam/8 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "7420549fb96f"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/team/8 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "11b565fa834b", "p2": "f01ad066d0be"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/probe-control/8, canary/probe-quiet/8, canary/probe-trap/8 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "11b565fa834b", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/hint/8 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "1ac45ed4d1ac", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo/9 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "176b4bc30aa9"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo-seam/9 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "b25913a8efaa"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/team/9 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "e4a072d26cce", "p2": "b0028d30d9e5"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/probe-control/9, canary/probe-quiet/9, canary/probe-trap/9 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "e4a072d26cce", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/hint/9 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "2a12f0f99a8a", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo/10 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "b2997d3052b0"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo-seam/10 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "02bb4869185c"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/team/10 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "3754049b828d", "p2": "dd073bd02e44"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/probe-control/10, canary/probe-quiet/10, canary/probe-trap/10 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "3754049b828d", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/hint/10 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "e9e40d8a5d11", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo/11 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "5a7ea0ee431a"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo-seam/11 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "760f5e9c9415"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/team/11 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "d558d7b2b6e4", "p2": "763e174cae5f"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/probe-control/11, canary/probe-quiet/11, canary/probe-trap/11 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "d558d7b2b6e4", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/hint/11 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "3af2ca8e3185", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo/12 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "46f25547e597"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo-seam/12 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "0601938b1176"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/team/12 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "8953f3d0a0df", "p2": "d971c7818a0a"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/probe-control/12, canary/probe-quiet/12, canary/probe-trap/12 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "8953f3d0a0df", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/hint/12 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "bd63c2fd300b", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo/13 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "39461932f549"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo-seam/13 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "aa3ed63074a9"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/team/13 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "81b15877e090", "p2": "988c6bca3441"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/probe-control/13, canary/probe-quiet/13, canary/probe-trap/13 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "81b15877e090", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/hint/13 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "ae4988732568", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo/14 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "ef191bbdb924"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo-seam/14 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "70234a78c2c3"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/team/14 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "5a9db8e65941", "p2": "3c72e35b456f"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/probe-control/14, canary/probe-quiet/14, canary/probe-trap/14 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "5a9db8e65941", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/hint/14 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "892138748a7c", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo/15 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "80212ee85c23"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo-seam/15 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "9712dcfaa4c1"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/team/15 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "559a22512ffe", "p2": "86efe320fb17"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/probe-control/15, canary/probe-quiet/15, canary/probe-trap/15 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "559a22512ffe", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/hint/15 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "544e4c0c5653", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo/16 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "6182311798d7"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo-seam/16 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "27549746d163"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/team/16 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "f000e686046b", "p2": "bc54063b3121"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/probe-control/16, canary/probe-quiet/16, canary/probe-trap/16 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "f000e686046b", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/hint/16 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "cc62365db3a7", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo/17 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "1118493baf30"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo-seam/17 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "975165d2036c"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/team/17 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "ad72ab5a0239", "p2": "514467956d38"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/probe-control/17, canary/probe-quiet/17, canary/probe-trap/17 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "ad72ab5a0239", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/hint/17 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "fc8b53346502", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo/18 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "293e848bb668"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo-seam/18 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "da229653445c"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/team/18 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "23d7ac9d2497", "p2": "66228892dce8"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/probe-control/18, canary/probe-quiet/18, canary/probe-trap/18 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "23d7ac9d2497", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/hint/18 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "1de3838ad703", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo/19 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "c53653854079"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo-seam/19 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "417835650cb1"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/team/19 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "98bac5873649", "p2": "16b4931f3048"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/probe-control/19, canary/probe-quiet/19, canary/probe-trap/19 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "98bac5873649", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/hint/19 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "2a4528bc976f", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo/20 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "9a0f2e66c697"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo-seam/20 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "d989d25a2d8d"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/team/20 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "6243627bff99", "p2": "d489ebee4835"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/probe-control/20, canary/probe-quiet/20, canary/probe-trap/20 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "6243627bff99", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/hint/20 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "5cede9a7ae35", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo/21 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "9ddc3d7c51aa"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo-seam/21 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "d3b4b1961215"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/team/21 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "7c68b21d65fa", "p2": "37f16ebcbaae"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/probe-control/21, canary/probe-quiet/21, canary/probe-trap/21 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "7c68b21d65fa", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/hint/21 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "a6cbd28e8849", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo/22 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "12e2d6a471af"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo-seam/22 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "59206153f285"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/team/22 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "9ebe1e21ff1a", "p2": "bbb6afffad55"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/probe-control/22, canary/probe-quiet/22, canary/probe-trap/22 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "9ebe1e21ff1a", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/hint/22 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "d4dbe31cfe1f", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo/23 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "457fe8ef09d9"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/solo-seam/23 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "8a53d26e2f5c"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/team/23 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "4caf44c6574b", "p2": "e3c51ef12b0d"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/probe-control/23, canary/probe-quiet/23, canary/probe-trap/23 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "4caf44c6574b", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| canary/hint/23 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "ff23edaa01ee", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo/0 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "a6adc2da2f26"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo-seam/0 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "d3dafbfd6f71"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/team/0 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "3d9a55cbd924", "p2": "9f4be270f771"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/probe-control/0, standard/probe-quiet/0, standard/probe-trap/0 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "3d9a55cbd924", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/hint/0 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "c6749b40ab73", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo/1 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "945dcb045b01"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo-seam/1 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "4c62bdb72a82"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/team/1 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "bb90d6832862", "p2": "2c15fb543c5f"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/probe-control/1, standard/probe-quiet/1, standard/probe-trap/1 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "bb90d6832862", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/hint/1 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "83cd7fb48e21", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo/2 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "8d50d6064566"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo-seam/2 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "1fe9a8234f23"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/team/2 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "3e6d50f639af", "p2": "af2c13f4f35f"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/probe-control/2, standard/probe-quiet/2, standard/probe-trap/2 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "3e6d50f639af", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/hint/2 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "eaadbe080be7", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo/3 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "d1afe1d88f84"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo-seam/3 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "dabdbb4f01f1"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/team/3 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "fd5b4f3215f9", "p2": "0c8f4e4af485"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/probe-control/3, standard/probe-quiet/3, standard/probe-trap/3 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "fd5b4f3215f9", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/hint/3 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "02a15d0edf63", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo/4 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "31418c599953"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo-seam/4 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "952a1313c7d2"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/team/4 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "68c90660e57c", "p2": "33805a6e667e"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/probe-control/4, standard/probe-quiet/4, standard/probe-trap/4 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "68c90660e57c", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/hint/4 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "9641ae0f4cd4", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo/5 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "21db1b33b4e1"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo-seam/5 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "d9e17305d612"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/team/5 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "436c30e63769", "p2": "fe27b3f10e6c"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/probe-control/5, standard/probe-quiet/5, standard/probe-trap/5 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "436c30e63769", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/hint/5 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "b84b16633659", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo/6 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "16012dfdae00"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo-seam/6 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "40808fe3ef7f"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/team/6 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "af132fa128ff", "p2": "73f0031ac856"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/probe-control/6, standard/probe-quiet/6, standard/probe-trap/6 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "af132fa128ff", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/hint/6 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "28a5811fb7cc", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo/7 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "9883f260940d"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo-seam/7 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "0b126172cb15"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/team/7 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "49bc71534d7f", "p2": "bb0311c46cae"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/probe-control/7, standard/probe-quiet/7, standard/probe-trap/7 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "49bc71534d7f", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/hint/7 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "6fd17f415707", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo/8 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "9d71dbf8366d"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo-seam/8 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "5b74dfbfb913"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/team/8 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "29431cd1e181", "p2": "5db5bbb49435"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/probe-control/8, standard/probe-quiet/8, standard/probe-trap/8 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "29431cd1e181", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/hint/8 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "668bc5753f90", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo/9 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "85da66004266"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo-seam/9 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "ee815446156f"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/team/9 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "fe6219953421", "p2": "ca788a3666bc"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/probe-control/9, standard/probe-quiet/9, standard/probe-trap/9 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "fe6219953421", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/hint/9 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "5c6c5d7aedd7", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo/10 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "359018971815"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo-seam/10 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "1d429b582381"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/team/10 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "26e9d44b4c82", "p2": "ca657cf3235c"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/probe-control/10, standard/probe-quiet/10, standard/probe-trap/10 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "26e9d44b4c82", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/hint/10 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "ef1b86e7f17f", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo/11 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "fc46227e61f7"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo-seam/11 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "d65f904b4ae4"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/team/11 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "76b29597dc10", "p2": "141a05e6b536"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/probe-control/11, standard/probe-quiet/11, standard/probe-trap/11 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "76b29597dc10", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/hint/11 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "40a77be54113", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo/12 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "c6c110b77043"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo-seam/12 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "798b0d86ae96"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/team/12 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "03018f61930e", "p2": "e3a1cee24b2b"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/probe-control/12, standard/probe-quiet/12, standard/probe-trap/12 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "03018f61930e", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/hint/12 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "e67c20117d9e", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo/13 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "6d5f895c8142"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo-seam/13 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "00b0b15dcd82"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/team/13 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "fbc1f211bb2d", "p2": "8f2d67630bbc"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/probe-control/13, standard/probe-quiet/13, standard/probe-trap/13 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "fbc1f211bb2d", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/hint/13 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "58032291e0d7", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo/14 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "b22af4c2b8e0"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo-seam/14 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "c066b5fa7c0d"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/team/14 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "5736571518d1", "p2": "000b07454992"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/probe-control/14, standard/probe-quiet/14, standard/probe-trap/14 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "5736571518d1", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/hint/14 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "ccf017af920d", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo/15 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "d755dfd8e846"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo-seam/15 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "dd69e67fd67b"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/team/15 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "8447f8fb85ae", "p2": "91083c60a42a"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/probe-control/15, standard/probe-quiet/15, standard/probe-trap/15 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "8447f8fb85ae", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/hint/15 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "79beeb09068f", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo/16 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "20c7a4ec784a"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo-seam/16 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "8deca5efe4fd"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/team/16 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "eb12dc1811e7", "p2": "f63296f85e7e"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/probe-control/16, standard/probe-quiet/16, standard/probe-trap/16 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "eb12dc1811e7", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/hint/16 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "3950cfb24d4d", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo/17 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "fba97f5e02df"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo-seam/17 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "d64b9462e4e1"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/team/17 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "9b493181ba98", "p2": "cabf545da505"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/probe-control/17, standard/probe-quiet/17, standard/probe-trap/17 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "9b493181ba98", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/hint/17 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "c3859709a9e8", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo/18 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "4df28b41077e"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo-seam/18 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "535d249c9abb"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/team/18 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "87b2033a73a7", "p2": "4d99000ecf2c"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/probe-control/18, standard/probe-quiet/18, standard/probe-trap/18 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "87b2033a73a7", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/hint/18 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "3744c42b5e92", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo/19 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "6d20fe8caeaa"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo-seam/19 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "b15014f2ae59"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/team/19 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "5c16abb71541", "p2": "e543e6f84a32"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/probe-control/19, standard/probe-quiet/19, standard/probe-trap/19 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "5c16abb71541", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/hint/19 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "1effcf224137", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo/20 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "754fd8107162"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo-seam/20 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "0f4cc004f36e"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/team/20 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "fae8c4306738", "p2": "227c304fcc04"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/probe-control/20, standard/probe-quiet/20, standard/probe-trap/20 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "fae8c4306738", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/hint/20 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "953f86bf7f6c", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo/21 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "505561226515"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo-seam/21 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "36bbad8024a5"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/team/21 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "0bd80bbbcbda", "p2": "55070922ee37"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/probe-control/21, standard/probe-quiet/21, standard/probe-trap/21 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "0bd80bbbcbda", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/hint/21 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "f1bb6c4f6b17", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo/22 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "3a4e42fc507d"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo-seam/22 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "6cc8113ccaaa"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/team/22 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "bf5f29ea6826", "p2": "fc7ed70be9be"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/probe-control/22, standard/probe-quiet/22, standard/probe-trap/22 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "bf5f29ea6826", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/hint/22 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "8157cbeb0ce2", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo/23 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "58e7f8bff7a4"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/solo-seam/23 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "b3dd271e356d"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/team/23 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "a58dedf30d54", "p2": "84b1b89a35b5"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/probe-control/23, standard/probe-quiet/23, standard/probe-trap/23 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "a58dedf30d54", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| standard/hint/23 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "415225462707", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo/0 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "171efbfe1a0b"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo-seam/0 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "9761a9f70965"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/team/0 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "5967e5bbad05", "p2": "708d3ccbc59b"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/probe-control/0, heavy/probe-quiet/0, heavy/probe-trap/0 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "5967e5bbad05", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/hint/0 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "73beddb1d45a", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo/1 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "8934a9403854"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo-seam/1 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "f12ad583f23a"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/team/1 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "3ccbda00af3e", "p2": "240ef7f8a4a9"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/probe-control/1, heavy/probe-quiet/1, heavy/probe-trap/1 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "3ccbda00af3e", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/hint/1 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "3552b88dbc3b", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo/2 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "06bce8e15560"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo-seam/2 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "4d999f6d57f3"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/team/2 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "24dd698ddc9a", "p2": "2df61a0fc8df"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/probe-control/2, heavy/probe-quiet/2, heavy/probe-trap/2 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "24dd698ddc9a", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/hint/2 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "bb5492eea0d6", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo/3 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "165a8e1948c5"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo-seam/3 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "e478409a116c"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/team/3 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "897db9ea1f26", "p2": "83f4b8c840aa"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/probe-control/3, heavy/probe-quiet/3, heavy/probe-trap/3 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "897db9ea1f26", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/hint/3 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "111b7b179c6a", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo/4 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "f8f541732695"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo-seam/4 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "f000fc732eb0"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/team/4 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "260bff329111", "p2": "9220505409ad"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/probe-control/4, heavy/probe-quiet/4, heavy/probe-trap/4 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "260bff329111", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/hint/4 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "b5ddca3f7b42", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo/5 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "0e530f30ae82"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo-seam/5 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "893683d0390c"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/team/5 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "84a3a695c2d5", "p2": "8d40a109e1b0"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/probe-control/5, heavy/probe-quiet/5, heavy/probe-trap/5 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "84a3a695c2d5", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/hint/5 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "8750804b5dc7", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo/6 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "d47e19d6a761"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo-seam/6 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "0016e002bd30"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/team/6 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "f3ecd5c76e91", "p2": "3a9942c9da89"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/probe-control/6, heavy/probe-quiet/6, heavy/probe-trap/6 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "f3ecd5c76e91", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/hint/6 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "6d551e2eddc6", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo/7 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "8ec28ca14ef8"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo-seam/7 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "737c6c651a91"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/team/7 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "390ae04c78c3", "p2": "e047e822adc1"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/probe-control/7, heavy/probe-quiet/7, heavy/probe-trap/7 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "390ae04c78c3", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/hint/7 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "9035390b7dd4", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo/8 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "72aacd3bb3a9"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo-seam/8 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "3cecdbd59d8d"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/team/8 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "adbd0e744c91", "p2": "478ea7f91d3f"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/probe-control/8, heavy/probe-quiet/8, heavy/probe-trap/8 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "adbd0e744c91", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/hint/8 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "c7ab5befa3b9", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo/9 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "fbe5fe53596d"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo-seam/9 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "734bd3911f60"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/team/9 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "35f3840214db", "p2": "1648a3073ab7"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/probe-control/9, heavy/probe-quiet/9, heavy/probe-trap/9 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "35f3840214db", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/hint/9 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "94686000bd01", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo/10 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "efc9dc3fc195"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo-seam/10 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "6dcca1e7b819"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/team/10 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "055ac2feac31", "p2": "c335ab534aa2"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/probe-control/10, heavy/probe-quiet/10, heavy/probe-trap/10 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "055ac2feac31", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/hint/10 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "1fb6ee6671b9", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo/11 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "8e2e456649f2"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo-seam/11 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "0dbde14a33bf"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/team/11 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "6f09d585123b", "p2": "15f9c8acb959"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/probe-control/11, heavy/probe-quiet/11, heavy/probe-trap/11 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "6f09d585123b", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/hint/11 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "b0aa789fa073", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo/12 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "9669a2f297b6"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo-seam/12 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "bfb77d09195e"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/team/12 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "ab0ce57c8be3", "p2": "2474a080a4c1"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/probe-control/12, heavy/probe-quiet/12, heavy/probe-trap/12 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "ab0ce57c8be3", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/hint/12 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "5e4a84565739", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo/13 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "6596dad85106"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo-seam/13 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "3e18a87138de"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/team/13 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "13b679544050", "p2": "2c8fc046ae47"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/probe-control/13, heavy/probe-quiet/13, heavy/probe-trap/13 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "13b679544050", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/hint/13 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "859456248cf1", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo/14 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "bc4df1cbe335"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo-seam/14 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "b3e6d2f22783"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/team/14 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "68e0569bda2d", "p2": "9b45b9a16d7a"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/probe-control/14, heavy/probe-quiet/14, heavy/probe-trap/14 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "68e0569bda2d", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/hint/14 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "224d07bb2bc0", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo/15 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "abf8c22dff57"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo-seam/15 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "99848e3bc319"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/team/15 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "58e858a9a24f", "p2": "5a472f352335"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/probe-control/15, heavy/probe-quiet/15, heavy/probe-trap/15 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "58e858a9a24f", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/hint/15 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "f3c61f1f133f", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo/16 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "318c94c30ff2"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo-seam/16 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "ceea3a59566e"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/team/16 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "84c2d037219d", "p2": "a9527975b671"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/probe-control/16, heavy/probe-quiet/16, heavy/probe-trap/16 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "84c2d037219d", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/hint/16 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "dc689d6d0c88", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo/17 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "7ce4590b62fb"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo-seam/17 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "0a81b6b9d51a"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/team/17 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "d78b18911532", "p2": "c106552e516f"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/probe-control/17, heavy/probe-quiet/17, heavy/probe-trap/17 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "d78b18911532", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/hint/17 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "490930529fcf", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo/18 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "586fbb001932"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo-seam/18 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "b76d308ff4ac"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/team/18 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "f781738311f3", "p2": "e86006df7976"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/probe-control/18, heavy/probe-quiet/18, heavy/probe-trap/18 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "f781738311f3", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/hint/18 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "016e7dc3a036", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo/19 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "da6d0a1abe56"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo-seam/19 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "bd0c48487242"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/team/19 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "53a51c7b94cc", "p2": "95df5c1a1239"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/probe-control/19, heavy/probe-quiet/19, heavy/probe-trap/19 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "53a51c7b94cc", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/hint/19 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "dec951e59139", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo/20 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "80ed857b06f7"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo-seam/20 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "e52f153ec880"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/team/20 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "961afbc9f26c", "p2": "b55e11f5e13a"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/probe-control/20, heavy/probe-quiet/20, heavy/probe-trap/20 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "961afbc9f26c", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/hint/20 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "6c6c669fc4d2", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo/21 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "a8ba004c1108"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo-seam/21 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "21003017157c"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/team/21 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "4b50d3308fdd", "p2": "3ce720bd572c"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/probe-control/21, heavy/probe-quiet/21, heavy/probe-trap/21 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "4b50d3308fdd", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/hint/21 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "02d9a7af7759", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo/22 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "c5207d706f16"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo-seam/22 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "fd062708555e"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/team/22 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "5b9b9c9d9337", "p2": "3d20ebecc53c"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/probe-control/22, heavy/probe-quiet/22, heavy/probe-trap/22 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "5b9b9c9d9337", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/hint/22 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "ef771d1c4138", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo/23 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "9bee2eaf5b9d"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/solo-seam/23 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "b57362d0ceef"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/team/23 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "c3cdf838b109", "p2": "c14dfd4c050b"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/probe-control/23, heavy/probe-quiet/23, heavy/probe-trap/23 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "c3cdf838b109", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |
| heavy/hint/23 | `{"extension": "8a636b7255c0", "family": "5a16f450e2a7", "family_version": 2, "instructions": {"p1": "817c860ed37e", "p2": "905bf5f95b45"}, "policies": "198791354800", "policy_runner": "5722b6365ad1", "world": "e17f7adb23c5"}` |

## Load and cost (estimates)

Estimate: 4 characters per input token; repeat the instruction, JSON briefing and seat files once per simulated world request (at least once per model seat). Excludes scripted seats, system prompts, replies, history and output tokens. Requests are not measured model turns.

| Level / arm | Per-seat max calls / briefing chars | Mean estimated tokens | $1 / $5 / $15 per M |
|---|---|---|---|
| canary / solo | p1: 31/713 | 25434 | $0.02543 / $0.12717 / $0.38151 |
| canary / solo-seam | p1: 31/584 | 27111 | $0.02711 / $0.13555 / $0.40666 |
| canary / team | p1: 31/584, p2: 1/83 | 27396 | $0.02740 / $0.13698 / $0.41095 |
| canary / probe-control | p1: 31/584, p2: 0/34 | 27104 | $0.02710 / $0.13552 / $0.40657 |
| canary / probe-trap | p1: 31/584, p2: 0/34 | 27104 | $0.02710 / $0.13552 / $0.40657 |
| canary / hint | p1: 31/584, p2: 0/34 | 27817 | $0.02782 / $0.13909 / $0.41726 |
| canary / probe-quiet | p1: 31/584, p2: 0/34 | 27104 | $0.02710 / $0.13552 / $0.40657 |
| standard / solo | p1: 31/830 | 27178 | $0.02718 / $0.13589 / $0.40767 |
| standard / solo-seam | p1: 31/582 | 27097 | $0.02710 / $0.13548 / $0.40645 |
| standard / team | p1: 31/582, p2: 1/131 | 27419 | $0.02742 / $0.13710 / $0.41129 |
| standard / probe-control | p1: 31/582, p2: 0/34 | 27088 | $0.02709 / $0.13544 / $0.40631 |
| standard / probe-trap | p1: 31/582, p2: 0/34 | 27088 | $0.02709 / $0.13544 / $0.40631 |
| standard / hint | p1: 31/582, p2: 0/34 | 27801 | $0.02780 / $0.13900 / $0.41701 |
| standard / probe-quiet | p1: 31/582, p2: 0/34 | 27088 | $0.02709 / $0.13544 / $0.40631 |
| heavy / solo | p1: 31/937 | 28902 | $0.02890 / $0.14451 / $0.43354 |
| heavy / solo-seam | p1: 31/584 | 27078 | $0.02708 / $0.13539 / $0.40618 |
| heavy / team | p1: 31/584, p2: 1/177 | 27446 | $0.02745 / $0.13723 / $0.41169 |
| heavy / probe-control | p1: 31/584, p2: 0/34 | 27075 | $0.02707 / $0.13537 / $0.40612 |
| heavy / probe-trap | p1: 31/584, p2: 0/34 | 27075 | $0.02707 / $0.13537 / $0.40612 |
| heavy / hint | p1: 31/584, p2: 0/34 | 27788 | $0.02779 / $0.13894 / $0.41681 |
| heavy / probe-quiet | p1: 31/584, p2: 0/34 | 27075 | $0.02707 / $0.13537 / $0.40612 |

## Evidence and predictions

[flotilla/docs/seams/commit.md](file:///Users/petermakhnatch/Developer/flotilla-wt/seams-commit/flotilla/docs/seams/commit.md)

## Open risks

- None flagged.

Scripted policy results are not estimates of frontier-model performance.
