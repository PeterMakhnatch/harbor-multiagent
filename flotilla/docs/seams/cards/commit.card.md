# EvalCard: commit — Book the day's outbound freight

**Seam:** Apply an outage update received during an ongoing batch before committing a final booking.
**Kit commit:** `b61f4c7e9820d49eee157abd033d4073182faeb7`; variant: `default`.
**Arms:** solo, solo-seam, team, probe-control, probe-trap, hint, probe-quiet. Levels: canary (canary), standard (standard), heavy (heavy).
**Sweep:** 24 instances × 3 schedule seeds. No model calls.
**Policies unavailable:** none.

## Policy matrix (mean / min / n; invalid count in parentheses)

| Level | Arm | Seats | careful | naive | nop | blind | random | lone | lapse-10 | lapse-20 | lapse-30 | lapse-50 | script-loop |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| canary | solo | p1 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.042 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| canary | solo-seam | p1 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.042 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.958 / 0.000 / 72 (0) | 0.833 / 0.000 / 72 (0) | 0.542 / 0.000 / 72 (0) | 0.375 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) |
| canary | team | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.042 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.917 / 0.000 / 72 (0) | 0.708 / 0.000 / 72 (0) | 0.417 / 0.000 / 72 (0) | 0.250 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) |
| canary | probe-control | p1, p2 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.042 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| canary | probe-trap | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.042 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.958 / 0.000 / 72 (0) | 0.833 / 0.000 / 72 (0) | 0.542 / 0.000 / 72 (0) | 0.375 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) |
| canary | hint | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.042 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.958 / 0.000 / 72 (0) | 0.833 / 0.000 / 72 (0) | 0.542 / 0.000 / 72 (0) | 0.375 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) |
| canary | probe-quiet | p1, p2 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard | solo | p1 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard | solo-seam | p1 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.875 / 0.000 / 72 (0) | 0.708 / 0.000 / 72 (0) | 0.667 / 0.000 / 72 (0) | 0.542 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) |
| standard | team | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.583 / 0.000 / 72 (0) | 0.167 / 0.000 / 72 (0) | 0.125 / 0.000 / 72 (0) | 0.167 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) |
| standard | probe-control | p1, p2 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard | probe-trap | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.875 / 0.000 / 72 (0) | 0.708 / 0.000 / 72 (0) | 0.667 / 0.000 / 72 (0) | 0.542 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) |
| standard | hint | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.875 / 0.000 / 72 (0) | 0.708 / 0.000 / 72 (0) | 0.667 / 0.000 / 72 (0) | 0.542 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) |
| standard | probe-quiet | p1, p2 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy | solo | p1 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy | solo-seam | p1 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.792 / 0.000 / 72 (0) | 0.542 / 0.000 / 72 (0) | 0.417 / 0.000 / 72 (0) | 0.125 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) |
| heavy | team | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.583 / 0.000 / 72 (0) | 0.250 / 0.000 / 72 (0) | 0.208 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) |
| heavy | probe-control | p1, p2 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy | probe-trap | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.792 / 0.000 / 72 (0) | 0.542 / 0.000 / 72 (0) | 0.417 / 0.000 / 72 (0) | 0.125 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) |
| heavy | hint | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.792 / 0.000 / 72 (0) | 0.542 / 0.000 / 72 (0) | 0.417 / 0.000 / 72 (0) | 0.125 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) |
| heavy | probe-quiet | p1, p2 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |

## Checks

| Check | Standard | Result | Evidence |
|---|---|---|---|
| levels | A5 | **PASS** | All requested dose levels available |
| solvable | A2/B1 | **PASS** | 1512 runs; careful reward = 1 |
| easy alone | A2 | **PASS** | 216 runs; solo careful reward = 1 |
| floor | A3 | **PASS** | 1512 runs; every nop reward ≤ 0.05 |
| chance | A3 | **PASS** | canary/team/blind: actual 0.041666666666666664, expected 0.0500 (absolute tolerance 0.1); canary/team/random: actual 0.0, expected 0.0000 (absolute tolerance 0.1); canary/probe-trap/blind: actual 0.041666666666666664, expected 0.0500 (absolute tolerance 0.1); canary/probe-trap/random: actual 0.0, expected 0.0000 (absolute tolerance 0.1); standard/team/blind: actual 0.0, expected 0.0000 (absolut… (full evidence in JSON) |
| separation | A4 | **PASS** | canary/probe-trap: gap 1.000; canary/probe-control: naive min 1.0; standard/probe-trap: gap 1.000; standard/probe-control: naive min 1.0; heavy/probe-trap: gap 1.000; heavy/probe-control: naive min 1.0 |
| dose response | A5/I1 | **PASS** | canary → standard → heavy: [0.0, 0.0, 0.0] |
| one seat is not enough | A3/C1 | **PASS** | 216 runs; every lone team reward ≤ 0.5 |
| schedule independence | E3 | **PASS** | 1512 runs; careful scores 1 across 3 schedule seed(s); max within-instance naive spread: 0.0 |
| simulation validity | F1 | **PASS** | 16632 runs; No invalid runs in the ordinary sweep |
| determinism | E1/E2 | **PASS** | 42/42 sample event hashes match |
| no leak | C1/D2 | **PASS** | 504 built tasks; seat views and family QA scanned |
| pairs | A1/D2 | **PASS** | probe-control→probe-quiet: 72 pairs, max 0 changed chars; fields: none; probe-control→probe-trap: 72 pairs, max 0 changed chars; fields: none; probe-trap→hint: 72 pairs, max 92 changed chars; fields: p1/instruction; probe-trap→solo-seam: 72 pairs, max 9 changed chars; fields: p1/instruction; probe-trap→team: 72 pairs, max 2197 changed chars; fields: p2/env.SEAT, p2/env.SEAT_BRIEFING, p2/env.WOR… (full evidence in JSON) |
| twin rule | A1 | **PASS** | Every team/trap has a declared twin |
| invalid is not zero | F1 | **PASS** | Budget-limited simulation: stalled=False, invalid=False, reward=0.0, stalled_seats=['p1'] (a cut-off play is void or a valid result that names the stalled seats, never an unflagged score) |
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
| canary | lapse-10 | boundary | solo − team | +0.083 | 72 / 24 | 0 |
| canary | lapse-10 | teammate | solo-seam − team | +0.042 | 72 / 24 | 0 |
| canary | lapse-10 | protocol | hint − team | +0.042 | 72 / 24 | 0 |
| canary | lapse-20 | boundary | solo − team | +0.292 | 72 / 24 | 0 |
| canary | lapse-20 | teammate | solo-seam − team | +0.125 | 72 / 24 | 0 |
| canary | lapse-20 | protocol | hint − team | +0.125 | 72 / 24 | 0 |
| canary | lapse-30 | boundary | solo − team | +0.583 | 72 / 24 | 0 |
| canary | lapse-30 | teammate | solo-seam − team | +0.125 | 72 / 24 | 0 |
| canary | lapse-30 | protocol | hint − team | +0.125 | 72 / 24 | 0 |
| canary | lapse-50 | boundary | solo − team | +0.750 | 72 / 24 | 0 |
| canary | lapse-50 | teammate | solo-seam − team | +0.125 | 72 / 24 | 0 |
| canary | lapse-50 | protocol | hint − team | +0.125 | 72 / 24 | 0 |
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
| standard | lapse-10 | boundary | solo − team | +0.417 | 72 / 24 | 0 |
| standard | lapse-10 | teammate | solo-seam − team | +0.292 | 72 / 24 | 0 |
| standard | lapse-10 | protocol | hint − team | +0.292 | 72 / 24 | 0 |
| standard | lapse-20 | boundary | solo − team | +0.833 | 72 / 24 | 0 |
| standard | lapse-20 | teammate | solo-seam − team | +0.542 | 72 / 24 | 0 |
| standard | lapse-20 | protocol | hint − team | +0.542 | 72 / 24 | 0 |
| standard | lapse-30 | boundary | solo − team | +0.875 | 72 / 24 | 0 |
| standard | lapse-30 | teammate | solo-seam − team | +0.542 | 72 / 24 | 0 |
| standard | lapse-30 | protocol | hint − team | +0.542 | 72 / 24 | 0 |
| standard | lapse-50 | boundary | solo − team | +0.833 | 72 / 24 | 0 |
| standard | lapse-50 | teammate | solo-seam − team | +0.375 | 72 / 24 | 0 |
| standard | lapse-50 | protocol | hint − team | +0.375 | 72 / 24 | 0 |
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
| heavy | lapse-10 | boundary | solo − team | +0.417 | 72 / 24 | 0 |
| heavy | lapse-10 | teammate | solo-seam − team | +0.208 | 72 / 24 | 0 |
| heavy | lapse-10 | protocol | hint − team | +0.208 | 72 / 24 | 0 |
| heavy | lapse-20 | boundary | solo − team | +0.750 | 72 / 24 | 0 |
| heavy | lapse-20 | teammate | solo-seam − team | +0.292 | 72 / 24 | 0 |
| heavy | lapse-20 | protocol | hint − team | +0.292 | 72 / 24 | 0 |
| heavy | lapse-30 | boundary | solo − team | +0.792 | 72 / 24 | 0 |
| heavy | lapse-30 | teammate | solo-seam − team | +0.208 | 72 / 24 | 0 |
| heavy | lapse-30 | protocol | hint − team | +0.208 | 72 / 24 | 0 |
| heavy | lapse-50 | boundary | solo − team | +1.000 | 72 / 24 | 0 |
| heavy | lapse-50 | teammate | solo-seam − team | +0.125 | 72 / 24 | 0 |
| heavy | lapse-50 | protocol | hint − team | +0.125 | 72 / 24 | 0 |
| heavy | script-loop | boundary | solo − team | +1.000 | 72 / 24 | 0 |
| heavy | script-loop | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | script-loop | protocol | hint − team | +0.000 | 72 / 24 | 0 |

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
| canary | lapse-30 | probe-control − probe-trap | +0.458 | +0.458 | 33 / 0 | 72 / 24 | 0 | 16 |
| canary | lapse-30 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| canary | lapse-50 | probe-control − probe-trap | +0.625 | +0.625 | 45 / 0 | 72 / 24 | 0 | 11 |
| canary | lapse-50 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| canary | script-loop | probe-control − probe-trap | +1.000 | +1.000 | 72 / 0 | 72 / 24 | 0 | 6 |
| canary | script-loop | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| standard | lapse-10 | probe-control − probe-trap | +0.125 | +0.125 | 9 / 0 | 72 / 24 | 0 | 62 |
| standard | lapse-10 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| standard | lapse-20 | probe-control − probe-trap | +0.292 | +0.292 | 21 / 0 | 72 / 24 | 0 | 26 |
| standard | lapse-20 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| standard | lapse-30 | probe-control − probe-trap | +0.333 | +0.333 | 24 / 0 | 72 / 24 | 0 | 23 |
| standard | lapse-30 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| standard | lapse-50 | probe-control − probe-trap | +0.458 | +0.458 | 33 / 0 | 72 / 24 | 0 | 16 |
| standard | lapse-50 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| standard | script-loop | probe-control − probe-trap | +1.000 | +1.000 | 72 / 0 | 72 / 24 | 0 | 6 |
| standard | script-loop | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| heavy | lapse-10 | probe-control − probe-trap | +0.208 | +0.208 | 15 / 0 | 72 / 24 | 0 | 37 |
| heavy | lapse-10 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| heavy | lapse-20 | probe-control − probe-trap | +0.458 | +0.458 | 33 / 0 | 72 / 24 | 0 | 16 |
| heavy | lapse-20 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| heavy | lapse-30 | probe-control − probe-trap | +0.583 | +0.583 | 42 / 0 | 72 / 24 | 0 | 12 |
| heavy | lapse-30 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| heavy | lapse-50 | probe-control − probe-trap | +0.875 | +0.875 | 63 / 0 | 72 / 24 | 0 | 8 |
| heavy | lapse-50 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| heavy | script-loop | probe-control − probe-trap | +1.000 | +1.000 | 72 / 0 | 72 / 24 | 0 | 6 |
| heavy | script-loop | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |

## Versions

| Task(s) | Simulator versions |
|---|---|
| canary/solo/0 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "4a4b0204dff7"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo-seam/0 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "2fde1c0f6a24"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/team/0 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "9d98da5eb7a6", "p2": "6b7e9d0c015f"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/probe-control/0, canary/probe-quiet/0, canary/probe-trap/0 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "9d98da5eb7a6", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/hint/0 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "d924db482e1e", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo/1 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "b55f9a7a53da"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo-seam/1 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "6fe9cc3a1f87"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/team/1 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "31a4a86316e1", "p2": "418d3adf40c5"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/probe-control/1, canary/probe-quiet/1, canary/probe-trap/1 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "31a4a86316e1", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/hint/1 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "09c963bcaf93", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo/2 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "4e25a5eba44c"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo-seam/2 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "3f7345efdd67"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/team/2 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "894a3a1c5dac", "p2": "d0fb0684a3d2"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/probe-control/2, canary/probe-quiet/2, canary/probe-trap/2 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "894a3a1c5dac", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/hint/2 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "2ba26fe2443b", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo/3 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "83d0f6d7e627"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo-seam/3 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "24ca70c82e8d"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/team/3 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "cb6e35a90fd8", "p2": "418d3adf40c5"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/probe-control/3, canary/probe-quiet/3, canary/probe-trap/3 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "cb6e35a90fd8", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/hint/3 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "ae61051e0b69", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo/4 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "ab3cbec04701"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo-seam/4 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "990a1a75a749"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/team/4 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "98f24a4a9a9f", "p2": "fde189521904"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/probe-control/4, canary/probe-quiet/4, canary/probe-trap/4 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "98f24a4a9a9f", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/hint/4 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "3fc41fefbdc3", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo/5 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "b5b75cfdcdc1"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo-seam/5 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "ce8fd16a1328"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/team/5 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "bc85f9cbf099", "p2": "1207b519e71d"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/probe-control/5, canary/probe-quiet/5, canary/probe-trap/5 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "bc85f9cbf099", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/hint/5 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "9db243f15cbb", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo/6 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "b6278dbaab69"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo-seam/6 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "d13c55003b5f"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/team/6 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "b420b4e9be66", "p2": "be0d1f298a45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/probe-control/6, canary/probe-quiet/6, canary/probe-trap/6 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "b420b4e9be66", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/hint/6 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "589eb8b246fd", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo/7 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "69b69f573c04"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo-seam/7 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "5afcffa64333"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/team/7 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "96c2cc32a070", "p2": "ce0f70eb5c28"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/probe-control/7, canary/probe-quiet/7, canary/probe-trap/7 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "96c2cc32a070", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/hint/7 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "b1c20d3d185c", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo/8 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "73cabe97897b"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo-seam/8 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "1b1672b640d8"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/team/8 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "56ba60d034c2", "p2": "7110b83bcf58"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/probe-control/8, canary/probe-quiet/8, canary/probe-trap/8 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "56ba60d034c2", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/hint/8 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "88dff57a3270", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo/9 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "91aa14deaeff"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo-seam/9 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "b5300643b7a7"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/team/9 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "233c000d83a3", "p2": "e29b150a941f"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/probe-control/9, canary/probe-quiet/9, canary/probe-trap/9 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "233c000d83a3", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/hint/9 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "15555377168f", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo/10 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "79957a12b29d"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo-seam/10 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "83d1bde2048f"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/team/10 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "786c309a7ef7", "p2": "aad8a26bc731"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/probe-control/10, canary/probe-quiet/10, canary/probe-trap/10 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "786c309a7ef7", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/hint/10 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "e24f9a88517e", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo/11 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "e11cafaa5d8b"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo-seam/11 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "ea4675e42777"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/team/11 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "e45ced651b26", "p2": "66b4b9d5c18f"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/probe-control/11, canary/probe-quiet/11, canary/probe-trap/11 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "e45ced651b26", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/hint/11 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "8036c3a44504", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo/12 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "c6c6fb4a36cc"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo-seam/12 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "52c200af393e"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/team/12 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "7d51a73a488e", "p2": "5014ad93813a"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/probe-control/12, canary/probe-quiet/12, canary/probe-trap/12 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "7d51a73a488e", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/hint/12 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "dc4639eea048", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo/13 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "43bc1f6b7656"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo-seam/13 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "3999b6b961f4"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/team/13 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "1adaee30111b", "p2": "e76dd5519a56"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/probe-control/13, canary/probe-quiet/13, canary/probe-trap/13 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "1adaee30111b", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/hint/13 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "4b3b07d48109", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo/14 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "d44bf3a0c018"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo-seam/14 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "698ee31236cb"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/team/14 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "486d2cd8f5ee", "p2": "a7224dc864ca"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/probe-control/14, canary/probe-quiet/14, canary/probe-trap/14 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "486d2cd8f5ee", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/hint/14 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "4663fef2a94a", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo/15 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "1f09dd6359e8"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo-seam/15 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "20bb5ff887df"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/team/15 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "e9ce83e0ca16", "p2": "cbbcfad7e4d0"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/probe-control/15, canary/probe-quiet/15, canary/probe-trap/15 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "e9ce83e0ca16", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/hint/15 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "2f5e6fe0d446", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo/16 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "144cd3cf8a3f"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo-seam/16 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "1b34125b51b0"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/team/16 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "65d7da8bc8f3", "p2": "cbbcfad7e4d0"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/probe-control/16, canary/probe-quiet/16, canary/probe-trap/16 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "65d7da8bc8f3", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/hint/16 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "d4c22ad1527a", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo/17 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "b6e8c8b886ee"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo-seam/17 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "cad5b2e575e2"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/team/17 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "43c8b63e3b31", "p2": "4b96c56c3dbe"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/probe-control/17, canary/probe-quiet/17, canary/probe-trap/17 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "43c8b63e3b31", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/hint/17 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "f28830743fe7", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo/18 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "a69f6a3aa942"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo-seam/18 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "ba2d20c1b32b"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/team/18 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "aa6641cadcc9", "p2": "cbbcfad7e4d0"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/probe-control/18, canary/probe-quiet/18, canary/probe-trap/18 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "aa6641cadcc9", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/hint/18 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "6cc3f991ec2f", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo/19 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "a142d5562f54"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo-seam/19 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "d73ae8f9559e"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/team/19 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "285d22bbacc2", "p2": "ea7d3c10f2bc"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/probe-control/19, canary/probe-quiet/19, canary/probe-trap/19 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "285d22bbacc2", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/hint/19 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "4ec6ca207326", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo/20 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "c933af20320c"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo-seam/20 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "9f514e006765"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/team/20 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "f589c7891a38", "p2": "f6e077a25ff8"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/probe-control/20, canary/probe-quiet/20, canary/probe-trap/20 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "f589c7891a38", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/hint/20 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "e02504dca14a", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo/21 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "8832d72a78a6"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo-seam/21 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "8043ea713548"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/team/21 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "9442cfc4b7d8", "p2": "de82284c0fcf"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/probe-control/21, canary/probe-quiet/21, canary/probe-trap/21 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "9442cfc4b7d8", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/hint/21 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "1b8b22e4c5d3", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo/22 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "f0a3f712756d"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo-seam/22 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "88d95562d000"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/team/22 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "8fd365a1bd83", "p2": "47789e00be0a"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/probe-control/22, canary/probe-quiet/22, canary/probe-trap/22 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "8fd365a1bd83", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/hint/22 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "4712aca9b120", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo/23 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "e43b9708a20d"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/solo-seam/23 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "ae1f59424297"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/team/23 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "efe792b0c554", "p2": "0b9f6ca9b727"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/probe-control/23, canary/probe-quiet/23, canary/probe-trap/23 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "efe792b0c554", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| canary/hint/23 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "259b6e64148e", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo/0 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "3b9cba7c061a"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo-seam/0 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "6074b8e59cbf"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/team/0 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "c1ccab2bf187", "p2": "693d6fdb244f"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/probe-control/0, standard/probe-quiet/0, standard/probe-trap/0 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "c1ccab2bf187", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/hint/0 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "1330585517a9", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo/1 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "d80f87f489ae"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo-seam/1 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "013bf687fa97"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/team/1 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "8f271a21e69d", "p2": "58e34dae3cdf"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/probe-control/1, standard/probe-quiet/1, standard/probe-trap/1 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "8f271a21e69d", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/hint/1 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "7883bec8be06", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo/2 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "eddcc2e3739f"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo-seam/2 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "bac55d1973b3"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/team/2 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "a82a8bbc7231", "p2": "a74a300545b6"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/probe-control/2, standard/probe-quiet/2, standard/probe-trap/2 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "a82a8bbc7231", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/hint/2 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "e84f0535f255", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo/3 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "e9b83ba7db4e"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo-seam/3 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "ca2a5ee3b326"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/team/3 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "40d9700e5604", "p2": "76f6a622dfdc"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/probe-control/3, standard/probe-quiet/3, standard/probe-trap/3 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "40d9700e5604", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/hint/3 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "74f8885e7537", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo/4 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "6b9dae2b3e16"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo-seam/4 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "207cfb6f2b61"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/team/4 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "39bd83592612", "p2": "81d31f0de71e"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/probe-control/4, standard/probe-quiet/4, standard/probe-trap/4 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "39bd83592612", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/hint/4 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "e3564fa4af4f", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo/5 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "e51423d8ec24"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo-seam/5 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "1b27abcc484c"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/team/5 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "0cc34742bb88", "p2": "30f0eff461a9"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/probe-control/5, standard/probe-quiet/5, standard/probe-trap/5 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "0cc34742bb88", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/hint/5 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "c0ee59dc64ea", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo/6 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "332e349e0793"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo-seam/6 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "2a5dfe039661"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/team/6 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "99fb3688bfa9", "p2": "9ec36dea6c2f"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/probe-control/6, standard/probe-quiet/6, standard/probe-trap/6 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "99fb3688bfa9", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/hint/6 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "b1ab5fbb5ecb", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo/7 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "59eda46b30c7"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo-seam/7 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "23370c2ff589"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/team/7 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "4314ca2dfb90", "p2": "06908edd1325"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/probe-control/7, standard/probe-quiet/7, standard/probe-trap/7 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "4314ca2dfb90", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/hint/7 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "a45a1b6416b7", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo/8 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "1c5e3446c6a4"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo-seam/8 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "f9e00e1f3183"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/team/8 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "b943057dc1c5", "p2": "f3dd542b3597"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/probe-control/8, standard/probe-quiet/8, standard/probe-trap/8 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "b943057dc1c5", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/hint/8 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "278515f939fa", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo/9 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "4aaa9330792e"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo-seam/9 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "402278f37c26"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/team/9 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "70c482a54691", "p2": "a240f1b1236a"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/probe-control/9, standard/probe-quiet/9, standard/probe-trap/9 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "70c482a54691", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/hint/9 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "f38a371d6036", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo/10 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "84d4536eb495"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo-seam/10 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "6894f9101d00"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/team/10 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "6cb02d63ddf0", "p2": "310c3bc6fd1b"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/probe-control/10, standard/probe-quiet/10, standard/probe-trap/10 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "6cb02d63ddf0", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/hint/10 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "cfaa6f745e99", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo/11 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "a9c82de1745f"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo-seam/11 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "dac6064340c0"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/team/11 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "eb27b013f8e0", "p2": "e49143bfb589"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/probe-control/11, standard/probe-quiet/11, standard/probe-trap/11 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "eb27b013f8e0", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/hint/11 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "07d9e2fc5637", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo/12 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "67905164bcb4"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo-seam/12 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "452890177359"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/team/12 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "632fc94a8e12", "p2": "1a1f5a14a755"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/probe-control/12, standard/probe-quiet/12, standard/probe-trap/12 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "632fc94a8e12", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/hint/12 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "05c3761a456b", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo/13 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "5ea799cff21d"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo-seam/13 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "e4e43d2765db"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/team/13 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "fe3ecb0a86f1", "p2": "b7d4672891e5"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/probe-control/13, standard/probe-quiet/13, standard/probe-trap/13 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "fe3ecb0a86f1", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/hint/13 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "4e5201129b36", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo/14 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "78edd7b6b050"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo-seam/14 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "68dc0eb03992"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/team/14 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "f1f3e36ebaad", "p2": "f612eeb98506"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/probe-control/14, standard/probe-quiet/14, standard/probe-trap/14 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "f1f3e36ebaad", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/hint/14 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "c361f510c5f0", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo/15 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "6912a96f30fa"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo-seam/15 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "02f8e051f095"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/team/15 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "8e79e8b9de6e", "p2": "5d71bbe496b5"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/probe-control/15, standard/probe-quiet/15, standard/probe-trap/15 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "8e79e8b9de6e", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/hint/15 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "47667924909a", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo/16 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "f5290be06386"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo-seam/16 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "9a0fc16a19ff"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/team/16 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "e89b092a355a", "p2": "19de1b606b2d"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/probe-control/16, standard/probe-quiet/16, standard/probe-trap/16 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "e89b092a355a", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/hint/16 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "9905d53520e3", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo/17 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "a730d981d9d3"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo-seam/17 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "31da3c45c945"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/team/17 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "fe9a934a1a75", "p2": "8a6dbdb1dc54"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/probe-control/17, standard/probe-quiet/17, standard/probe-trap/17 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "fe9a934a1a75", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/hint/17 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "3eb2fbfd0edf", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo/18 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "baaf4bd85e1d"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo-seam/18 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "f2bdef42b238"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/team/18 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "839f4eac3e63", "p2": "a795f1f021e2"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/probe-control/18, standard/probe-quiet/18, standard/probe-trap/18 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "839f4eac3e63", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/hint/18 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "953cf7d8ce62", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo/19 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "504e61337e63"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo-seam/19 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "d77d4c90ff14"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/team/19 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "6351ee6283f8", "p2": "926f888cd67c"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/probe-control/19, standard/probe-quiet/19, standard/probe-trap/19 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "6351ee6283f8", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/hint/19 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "4f5e4f73a24a", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo/20 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "5b1299d92a08"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo-seam/20 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "845f29e4889d"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/team/20 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "e0fa03d99cd2", "p2": "71c9a6799a47"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/probe-control/20, standard/probe-quiet/20, standard/probe-trap/20 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "e0fa03d99cd2", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/hint/20 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "eb8a7f5b2882", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo/21 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "2f846049a24c"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo-seam/21 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "2a2907281863"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/team/21 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "3b0dce1bb1aa", "p2": "327e7ca01a80"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/probe-control/21, standard/probe-quiet/21, standard/probe-trap/21 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "3b0dce1bb1aa", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/hint/21 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "6cbcd2c7383d", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo/22 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "39cf37a2f57d"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo-seam/22 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "b6d5f8e1bda3"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/team/22 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "0cfb7bd2aa3c", "p2": "82c8fe7f04ca"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/probe-control/22, standard/probe-quiet/22, standard/probe-trap/22 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "0cfb7bd2aa3c", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/hint/22 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "62ec166ef5a1", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo/23 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "0154a758db6d"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/solo-seam/23 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "03b81343ec0a"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/team/23 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "392cff98d09d", "p2": "738908ca236d"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/probe-control/23, standard/probe-quiet/23, standard/probe-trap/23 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "392cff98d09d", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| standard/hint/23 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "529215491a82", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo/0 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "18dd5c67391a"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo-seam/0 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "92fba132718a"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/team/0 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "5f5889dcb98a", "p2": "75fcb27552d5"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/probe-control/0, heavy/probe-quiet/0, heavy/probe-trap/0 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "5f5889dcb98a", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/hint/0 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "e796ec80b280", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo/1 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "92a10e5923f9"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo-seam/1 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "e89f76735c4c"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/team/1 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "b96771ae04a8", "p2": "1249e9dec2fd"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/probe-control/1, heavy/probe-quiet/1, heavy/probe-trap/1 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "b96771ae04a8", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/hint/1 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "5d8458dd1a13", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo/2 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "e8ab52d6b685"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo-seam/2 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "5b0fe5241f20"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/team/2 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "3efeef8bdce6", "p2": "1cf5f0be7839"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/probe-control/2, heavy/probe-quiet/2, heavy/probe-trap/2 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "3efeef8bdce6", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/hint/2 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "b29c1c92d807", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo/3 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "2ec37426d3d8"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo-seam/3 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "84cc530bf42f"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/team/3 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "e0591ce8436a", "p2": "351fec43e18e"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/probe-control/3, heavy/probe-quiet/3, heavy/probe-trap/3 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "e0591ce8436a", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/hint/3 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "ac676498f8fe", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo/4 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "3435264a35b6"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo-seam/4 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "f9897a945f5f"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/team/4 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "82f006ebc25c", "p2": "b0a00d382538"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/probe-control/4, heavy/probe-quiet/4, heavy/probe-trap/4 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "82f006ebc25c", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/hint/4 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "97d48d00ef5b", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo/5 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "8322eec20a92"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo-seam/5 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "32ca2e7fac28"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/team/5 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "9ea9c6225861", "p2": "4ac23e2fbd1d"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/probe-control/5, heavy/probe-quiet/5, heavy/probe-trap/5 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "9ea9c6225861", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/hint/5 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "e88f4c0bd4e4", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo/6 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "a8bc2d70f373"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo-seam/6 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "da0d015315eb"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/team/6 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "fece20d899ee", "p2": "09ead36eab88"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/probe-control/6, heavy/probe-quiet/6, heavy/probe-trap/6 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "fece20d899ee", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/hint/6 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "d56dec549747", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo/7 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "706e836cf6fc"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo-seam/7 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "135eb030d190"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/team/7 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "64dbb40122e8", "p2": "3f8712300d2e"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/probe-control/7, heavy/probe-quiet/7, heavy/probe-trap/7 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "64dbb40122e8", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/hint/7 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "dc929acc5b1d", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo/8 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "e652ab2358e8"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo-seam/8 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "d8ba6262bebb"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/team/8 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "6fb54d9a6204", "p2": "a4ffbc630ae7"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/probe-control/8, heavy/probe-quiet/8, heavy/probe-trap/8 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "6fb54d9a6204", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/hint/8 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "b3138ebf5a00", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo/9 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "03db03728678"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo-seam/9 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "2a4a13e13ff3"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/team/9 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "01bf953431d5", "p2": "2778384db817"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/probe-control/9, heavy/probe-quiet/9, heavy/probe-trap/9 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "01bf953431d5", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/hint/9 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "f4eba0f0c27e", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo/10 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "45616cfef309"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo-seam/10 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "dab59b228fbb"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/team/10 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "4f3d50cb25f4", "p2": "5976637163e6"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/probe-control/10, heavy/probe-quiet/10, heavy/probe-trap/10 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "4f3d50cb25f4", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/hint/10 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "9042c735f6e2", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo/11 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "b2518df6724f"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo-seam/11 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "b91d0c04c705"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/team/11 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "d11be90ca0be", "p2": "6eaafd3535bf"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/probe-control/11, heavy/probe-quiet/11, heavy/probe-trap/11 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "d11be90ca0be", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/hint/11 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "b7e8d0a9f62b", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo/12 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "26eb7d57cade"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo-seam/12 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "eb437e667533"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/team/12 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "c401f99215ec", "p2": "929997154d74"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/probe-control/12, heavy/probe-quiet/12, heavy/probe-trap/12 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "c401f99215ec", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/hint/12 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "bf4ad1dc8819", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo/13 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "8772787ee804"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo-seam/13 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "ebe5f25ffff9"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/team/13 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "f4336d57a5a5", "p2": "758ee483d8a0"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/probe-control/13, heavy/probe-quiet/13, heavy/probe-trap/13 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "f4336d57a5a5", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/hint/13 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "d84406437f3e", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo/14 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "2b24f9057c2a"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo-seam/14 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "03aa2bd2026d"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/team/14 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "eae547eb47c5", "p2": "888b92fe5e8f"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/probe-control/14, heavy/probe-quiet/14, heavy/probe-trap/14 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "eae547eb47c5", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/hint/14 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "9e3ea4ef5ec3", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo/15 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "6308046fc0c3"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo-seam/15 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "7f878f249230"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/team/15 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "2a874381c6d3", "p2": "8990967fb084"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/probe-control/15, heavy/probe-quiet/15, heavy/probe-trap/15 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "2a874381c6d3", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/hint/15 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "bb722bb790a6", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo/16 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "2d9a653ba614"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo-seam/16 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "4aab9be8a44c"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/team/16 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "b2ad0503f0a4", "p2": "6089c885c95d"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/probe-control/16, heavy/probe-quiet/16, heavy/probe-trap/16 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "b2ad0503f0a4", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/hint/16 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "8c629a9e2148", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo/17 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "1d65862f0425"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo-seam/17 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "dbdd4ee85bc4"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/team/17 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "b5acea7617be", "p2": "39d5233f1031"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/probe-control/17, heavy/probe-quiet/17, heavy/probe-trap/17 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "b5acea7617be", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/hint/17 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "f50870b931e6", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo/18 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "1293e8b15393"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo-seam/18 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "2a9a0b1df487"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/team/18 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "4f1fe5d951ff", "p2": "66ec0558c034"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/probe-control/18, heavy/probe-quiet/18, heavy/probe-trap/18 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "4f1fe5d951ff", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/hint/18 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "90595eae511e", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo/19 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "d54d1fbf3838"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo-seam/19 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "7d9126ab4989"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/team/19 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "f9f2d10245e2", "p2": "54d94c53a2bb"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/probe-control/19, heavy/probe-quiet/19, heavy/probe-trap/19 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "f9f2d10245e2", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/hint/19 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "bd13c44e2c13", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo/20 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "c719e2647526"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo-seam/20 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "e2758d799ab7"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/team/20 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "5f2ab5d71c75", "p2": "b79026c49d3c"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/probe-control/20, heavy/probe-quiet/20, heavy/probe-trap/20 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "5f2ab5d71c75", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/hint/20 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "fb40143d0391", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo/21 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "440d6bedad2c"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo-seam/21 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "d7f6dd79da0e"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/team/21 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "7c126bd3946b", "p2": "b071c30cde40"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/probe-control/21, heavy/probe-quiet/21, heavy/probe-trap/21 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "7c126bd3946b", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/hint/21 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "695a2749bca7", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo/22 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "183c5927ee1b"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo-seam/22 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "55f6e036b2e6"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/team/22 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "8c344e96775a", "p2": "db03d6a2df09"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/probe-control/22, heavy/probe-quiet/22, heavy/probe-trap/22 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "8c344e96775a", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/hint/22 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "55e31ccbe11c", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo/23 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "e874032af382"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/solo-seam/23 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "0ea9d5f8d044"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/team/23 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "de5e78dcf1b0", "p2": "aea2ad88a9dd"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/probe-control/23, heavy/probe-quiet/23, heavy/probe-trap/23 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "de5e78dcf1b0", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |
| heavy/hint/23 | `{"extension": "af5bf101b560", "family": "087b05a81444", "family_version": 3, "instructions": {"p1": "1bb414852e61", "p2": "905bf5f95b45"}, "policies": "b0dd60a2920d", "policy_runner": "5722b6365ad1", "world": "6a0593e2a63b"}` |

## Load and cost (estimates)

Estimate: 4 characters per input token; repeat the instruction, JSON briefing and seat files once per simulated world request (at least once per model seat). Excludes scripted seats, system prompts, replies, history and output tokens. Requests are not measured model turns.

| Level / arm | Per-seat max calls / briefing chars | Mean estimated tokens | $1 / $5 / $15 per M |
|---|---|---|---|
| canary / solo | p1: 31/693 | 27733 | $0.02773 / $0.13867 / $0.41600 |
| canary / solo-seam | p1: 33/564 | 33011 | $0.03301 / $0.16506 / $0.49517 |
| canary / team | p1: 33/564, p2: 1/83 | 33384 | $0.03338 / $0.16692 / $0.50075 |
| canary / probe-control | p1: 33/564, p2: 0/34 | 33011 | $0.03301 / $0.16506 / $0.49517 |
| canary / probe-trap | p1: 33/564, p2: 0/34 | 33011 | $0.03301 / $0.16506 / $0.49517 |
| canary / hint | p1: 33/564, p2: 0/34 | 33770 | $0.03377 / $0.16885 / $0.50655 |
| canary / probe-quiet | p1: 33/564, p2: 0/34 | 33011 | $0.03301 / $0.16506 / $0.49517 |
| standard / solo | p1: 31/815 | 29552 | $0.02955 / $0.14776 / $0.44328 |
| standard / solo-seam | p1: 33/568 | 33088 | $0.03309 / $0.16544 / $0.49632 |
| standard / team | p1: 33/568, p2: 1/130 | 33496 | $0.03350 / $0.16748 / $0.50244 |
| standard / probe-control | p1: 33/568, p2: 0/34 | 33084 | $0.03308 / $0.16542 / $0.49626 |
| standard / probe-trap | p1: 33/568, p2: 0/34 | 33084 | $0.03308 / $0.16542 / $0.49626 |
| standard / hint | p1: 33/568, p2: 0/34 | 33843 | $0.03384 / $0.16921 / $0.50764 |
| standard / probe-quiet | p1: 33/568, p2: 0/34 | 33084 | $0.03308 / $0.16542 / $0.49626 |
| heavy / solo | p1: 31/928 | 31240 | $0.03124 / $0.15620 / $0.46860 |
| heavy / solo-seam | p1: 33/566 | 33023 | $0.03302 / $0.16512 / $0.49535 |
| heavy / team | p1: 33/566, p2: 1/179 | 33469 | $0.03347 / $0.16735 / $0.50204 |
| heavy / probe-control | p1: 33/566, p2: 0/34 | 33018 | $0.03302 / $0.16509 / $0.49527 |
| heavy / probe-trap | p1: 33/566, p2: 0/34 | 33018 | $0.03302 / $0.16509 / $0.49527 |
| heavy / hint | p1: 33/566, p2: 0/34 | 33777 | $0.03378 / $0.16888 / $0.50665 |
| heavy / probe-quiet | p1: 33/566, p2: 0/34 | 33018 | $0.03302 / $0.16509 / $0.49527 |

## Evidence and predictions

[flotilla/docs/seams/commit.md](file:///Users/petermakhnatch/Developer/flotilla-wt/seams-commit/flotilla/docs/seams/commit.md)

## Open risks

- None flagged.

Scripted policy results are not estimates of frontier-model performance.
