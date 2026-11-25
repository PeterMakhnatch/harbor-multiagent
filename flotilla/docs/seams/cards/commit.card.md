# EvalCard: commit — Book the day's outbound freight

**Seam:** Apply an outage update received during an ongoing batch before committing a final booking.
**Kit commit:** `61807695faa411cbfe8a0334dbfcc4dfb609228a`; variant: `default`.
**Arms:** solo, solo-seam, team, probe-control, probe-trap, hint, probe-quiet. Levels: canary (canary), standard (standard), heavy (heavy), standard-bulk (standard-bulk), heavy-bulk (heavy-bulk).
**Sweep:** 24 instances × 3 schedule seeds. No model calls.
**Policies unavailable:** none.

## Policy matrix (mean / min / n; invalid count in parentheses)

| Level | Arm | Seats | careful | naive | nop | blind | random | lone | script-loop | script-filter | script-check | lapse-10 | lapse-20 | lapse-30 | lapse-50 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| canary | solo | p1 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.083 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| canary | solo-seam | p1 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.083 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.917 / 0.000 / 72 (0) | 0.625 / 0.000 / 72 (0) | 0.708 / 0.000 / 72 (0) | 0.500 / 0.000 / 72 (0) |
| canary | team | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.083 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.792 / 0.000 / 72 (0) | 0.500 / 0.000 / 72 (0) | 0.583 / 0.000 / 72 (0) | 0.292 / 0.000 / 72 (0) |
| canary | probe-control | p1, p2 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.083 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| canary | probe-trap | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.083 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.917 / 0.000 / 72 (0) | 0.625 / 0.000 / 72 (0) | 0.708 / 0.000 / 72 (0) | 0.500 / 0.000 / 72 (0) |
| canary | hint | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.083 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.917 / 0.000 / 72 (0) | 0.625 / 0.000 / 72 (0) | 0.708 / 0.000 / 72 (0) | 0.500 / 0.000 / 72 (0) |
| canary | probe-quiet | p1, p2 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard | solo | p1 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard | solo-seam | p1 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.708 / 0.000 / 72 (0) | 0.792 / 0.000 / 72 (0) | 0.375 / 0.000 / 72 (0) | 0.333 / 0.000 / 72 (0) |
| standard | team | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.375 / 0.000 / 72 (0) | 0.583 / 0.000 / 72 (0) | 0.125 / 0.000 / 72 (0) | 0.083 / 0.000 / 72 (0) |
| standard | probe-control | p1, p2 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard | probe-trap | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.708 / 0.000 / 72 (0) | 0.792 / 0.000 / 72 (0) | 0.375 / 0.000 / 72 (0) | 0.333 / 0.000 / 72 (0) |
| standard | hint | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.708 / 0.000 / 72 (0) | 0.792 / 0.000 / 72 (0) | 0.375 / 0.000 / 72 (0) | 0.333 / 0.000 / 72 (0) |
| standard | probe-quiet | p1, p2 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy | solo | p1 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy | solo-seam | p1 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.750 / 0.000 / 72 (0) | 0.542 / 0.000 / 72 (0) | 0.375 / 0.000 / 72 (0) | 0.125 / 0.000 / 72 (0) |
| heavy | team | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.583 / 0.000 / 72 (0) | 0.208 / 0.000 / 72 (0) | 0.083 / 0.000 / 72 (0) | 0.042 / 0.000 / 72 (0) |
| heavy | probe-control | p1, p2 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy | probe-trap | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.750 / 0.000 / 72 (0) | 0.542 / 0.000 / 72 (0) | 0.375 / 0.000 / 72 (0) | 0.125 / 0.000 / 72 (0) |
| heavy | hint | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.750 / 0.000 / 72 (0) | 0.542 / 0.000 / 72 (0) | 0.375 / 0.000 / 72 (0) | 0.125 / 0.000 / 72 (0) |
| heavy | probe-quiet | p1, p2 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard-bulk | solo | p1 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard-bulk | solo-seam | p1 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.708 / 0.000 / 72 (0) | 0.792 / 0.000 / 72 (0) | 0.375 / 0.000 / 72 (0) | 0.333 / 0.000 / 72 (0) |
| standard-bulk | team | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.375 / 0.000 / 72 (0) | 0.583 / 0.000 / 72 (0) | 0.125 / 0.000 / 72 (0) | 0.083 / 0.000 / 72 (0) |
| standard-bulk | probe-control | p1, p2 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| standard-bulk | probe-trap | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.708 / 0.000 / 72 (0) | 0.792 / 0.000 / 72 (0) | 0.375 / 0.000 / 72 (0) | 0.333 / 0.000 / 72 (0) |
| standard-bulk | hint | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.708 / 0.000 / 72 (0) | 0.792 / 0.000 / 72 (0) | 0.375 / 0.000 / 72 (0) | 0.333 / 0.000 / 72 (0) |
| standard-bulk | probe-quiet | p1, p2 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy-bulk | solo | p1 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy-bulk | solo-seam | p1 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.750 / 0.000 / 72 (0) | 0.542 / 0.000 / 72 (0) | 0.375 / 0.000 / 72 (0) | 0.125 / 0.000 / 72 (0) |
| heavy-bulk | team | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.583 / 0.000 / 72 (0) | 0.208 / 0.000 / 72 (0) | 0.083 / 0.000 / 72 (0) | 0.042 / 0.000 / 72 (0) |
| heavy-bulk | probe-control | p1, p2 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |
| heavy-bulk | probe-trap | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.750 / 0.000 / 72 (0) | 0.542 / 0.000 / 72 (0) | 0.375 / 0.000 / 72 (0) | 0.125 / 0.000 / 72 (0) |
| heavy-bulk | hint | p1, p2 | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.750 / 0.000 / 72 (0) | 0.542 / 0.000 / 72 (0) | 0.375 / 0.000 / 72 (0) | 0.125 / 0.000 / 72 (0) |
| heavy-bulk | probe-quiet | p1, p2 | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) | 1.000 / 1.000 / 72 (0) |

## Checks

| Check | Standard | Result | Evidence |
|---|---|---|---|
| levels | A5 | **PASS** | All requested dose levels available |
| solvable | A2/B1 | **PASS** | 2520 runs; careful reward = 1 |
| easy alone | A2 | **PASS** | 360 runs; solo careful reward = 1 |
| floor | A3 | **PASS** | 2520 runs; every nop reward ≤ 0.05 |
| chance | A3 | **PASS** | canary/team/blind: actual 0.08333333333333333, expected 0.0500 (absolute tolerance 0.1); canary/team/random: actual 0.0, expected 0.0000 (absolute tolerance 0.1); canary/probe-trap/blind: actual 0.08333333333333333, expected 0.0500 (absolute tolerance 0.1); canary/probe-trap/random: actual 0.0, expected 0.0000 (absolute tolerance 0.1); standard/team/blind: actual 0.0, expected 0.0000 (absolute … (full evidence in JSON) |
| separation | A4 | **PASS** | canary/probe-trap: gap 1.000; canary/probe-control: naive min 1.0; standard/probe-trap: gap 1.000; standard/probe-control: naive min 1.0; heavy/probe-trap: gap 1.000; heavy/probe-control: naive min 1.0; standard-bulk/probe-trap: gap 1.000; standard-bulk/probe-control: naive min 1.0; heavy-bulk/probe-trap: gap 1.000; heavy-bulk/probe-control: naive min 1.0 |
| dose response | A5/I1 | **PASS** | canary → standard → heavy → standard-bulk → heavy-bulk: [0.0, 0.0, 0.0, 0.0, 0.0] |
| one seat is not enough | A3/C1 | **PASS** | 360 runs; every lone team reward ≤ 0.5 |
| schedule independence | E3 | **PASS** | 2520 runs; careful scores 1 across 3 schedule seed(s); max within-instance naive spread: 0.0 |
| simulation validity | F1 | **PASS** | 32760 runs; No invalid runs in the ordinary sweep |
| determinism | E1/E2 | **PASS** | 70/70 sample event hashes match |
| no leak | C1/D2 | **PASS** | 840 built tasks; seat views and family QA scanned |
| pairs | A1/D2 | **PASS** | probe-control→probe-quiet: 120 pairs, max 0 changed chars; fields: none; probe-control→probe-trap: 120 pairs, max 0 changed chars; fields: none; probe-trap→hint: 120 pairs, max 92 changed chars; fields: p1/instruction; probe-trap→solo-seam: 120 pairs, max 9 changed chars; fields: p1/instruction; probe-trap→team: 120 pairs, max 2096 changed chars; fields: p2/env.SEAT, p2/env.SEAT_BRIEFING, p2/en… (full evidence in JSON) |
| twin rule | A1 | **PASS** | Every team/trap has a declared twin |
| invalid is not zero | F1 | **PASS** | Budget-limited simulation: stalled=False, invalid=False, reward=0.0, stalled_seats=['p1'] (a cut-off play is void or a valid result that names the stalled seats, never an unflagged score) |
| canary | A6 | **PASS** | Marker checked in 840 task files and all seat views |
| power plan | A3 | **PASS** | 50 paired comparisons; fixed exact McNemar plan (see table) |

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
| canary | script-loop | boundary | solo − team | +1.000 | 72 / 24 | 0 |
| canary | script-loop | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | script-loop | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | script-filter | boundary | solo − team | +1.000 | 72 / 24 | 0 |
| canary | script-filter | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | script-filter | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | script-check | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| canary | script-check | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| canary | script-check | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-10 | boundary | solo − team | +0.208 | 72 / 24 | 0 |
| canary | lapse-10 | teammate | solo-seam − team | +0.125 | 72 / 24 | 0 |
| canary | lapse-10 | protocol | hint − team | +0.125 | 72 / 24 | 0 |
| canary | lapse-20 | boundary | solo − team | +0.500 | 72 / 24 | 0 |
| canary | lapse-20 | teammate | solo-seam − team | +0.125 | 72 / 24 | 0 |
| canary | lapse-20 | protocol | hint − team | +0.125 | 72 / 24 | 0 |
| canary | lapse-30 | boundary | solo − team | +0.417 | 72 / 24 | 0 |
| canary | lapse-30 | teammate | solo-seam − team | +0.125 | 72 / 24 | 0 |
| canary | lapse-30 | protocol | hint − team | +0.125 | 72 / 24 | 0 |
| canary | lapse-50 | boundary | solo − team | +0.708 | 72 / 24 | 0 |
| canary | lapse-50 | teammate | solo-seam − team | +0.208 | 72 / 24 | 0 |
| canary | lapse-50 | protocol | hint − team | +0.208 | 72 / 24 | 0 |
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
| standard | script-loop | boundary | solo − team | +1.000 | 72 / 24 | 0 |
| standard | script-loop | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | script-loop | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | script-filter | boundary | solo − team | +1.000 | 72 / 24 | 0 |
| standard | script-filter | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | script-filter | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | script-check | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| standard | script-check | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard | script-check | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-10 | boundary | solo − team | +0.625 | 72 / 24 | 0 |
| standard | lapse-10 | teammate | solo-seam − team | +0.333 | 72 / 24 | 0 |
| standard | lapse-10 | protocol | hint − team | +0.333 | 72 / 24 | 0 |
| standard | lapse-20 | boundary | solo − team | +0.417 | 72 / 24 | 0 |
| standard | lapse-20 | teammate | solo-seam − team | +0.208 | 72 / 24 | 0 |
| standard | lapse-20 | protocol | hint − team | +0.208 | 72 / 24 | 0 |
| standard | lapse-30 | boundary | solo − team | +0.875 | 72 / 24 | 0 |
| standard | lapse-30 | teammate | solo-seam − team | +0.250 | 72 / 24 | 0 |
| standard | lapse-30 | protocol | hint − team | +0.250 | 72 / 24 | 0 |
| standard | lapse-50 | boundary | solo − team | +0.917 | 72 / 24 | 0 |
| standard | lapse-50 | teammate | solo-seam − team | +0.250 | 72 / 24 | 0 |
| standard | lapse-50 | protocol | hint − team | +0.250 | 72 / 24 | 0 |
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
| heavy | script-loop | boundary | solo − team | +1.000 | 72 / 24 | 0 |
| heavy | script-loop | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | script-loop | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | script-filter | boundary | solo − team | +1.000 | 72 / 24 | 0 |
| heavy | script-filter | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | script-filter | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | script-check | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| heavy | script-check | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy | script-check | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy | lapse-10 | boundary | solo − team | +0.417 | 72 / 24 | 0 |
| heavy | lapse-10 | teammate | solo-seam − team | +0.167 | 72 / 24 | 0 |
| heavy | lapse-10 | protocol | hint − team | +0.167 | 72 / 24 | 0 |
| heavy | lapse-20 | boundary | solo − team | +0.792 | 72 / 24 | 0 |
| heavy | lapse-20 | teammate | solo-seam − team | +0.333 | 72 / 24 | 0 |
| heavy | lapse-20 | protocol | hint − team | +0.333 | 72 / 24 | 0 |
| heavy | lapse-30 | boundary | solo − team | +0.917 | 72 / 24 | 0 |
| heavy | lapse-30 | teammate | solo-seam − team | +0.292 | 72 / 24 | 0 |
| heavy | lapse-30 | protocol | hint − team | +0.292 | 72 / 24 | 0 |
| heavy | lapse-50 | boundary | solo − team | +0.958 | 72 / 24 | 0 |
| heavy | lapse-50 | teammate | solo-seam − team | +0.083 | 72 / 24 | 0 |
| heavy | lapse-50 | protocol | hint − team | +0.083 | 72 / 24 | 0 |
| standard-bulk | careful | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| standard-bulk | careful | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard-bulk | careful | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard-bulk | naive | boundary | solo − team | +1.000 | 72 / 24 | 0 |
| standard-bulk | naive | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard-bulk | naive | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard-bulk | nop | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| standard-bulk | nop | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard-bulk | nop | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard-bulk | blind | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| standard-bulk | blind | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard-bulk | blind | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard-bulk | random | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| standard-bulk | random | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard-bulk | random | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard-bulk | lone | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| standard-bulk | lone | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard-bulk | lone | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard-bulk | script-loop | boundary | solo − team | +1.000 | 72 / 24 | 0 |
| standard-bulk | script-loop | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard-bulk | script-loop | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard-bulk | script-filter | boundary | solo − team | +1.000 | 72 / 24 | 0 |
| standard-bulk | script-filter | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard-bulk | script-filter | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard-bulk | script-check | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| standard-bulk | script-check | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| standard-bulk | script-check | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard-bulk | lapse-10 | boundary | solo − team | +0.625 | 72 / 24 | 0 |
| standard-bulk | lapse-10 | teammate | solo-seam − team | +0.333 | 72 / 24 | 0 |
| standard-bulk | lapse-10 | protocol | hint − team | +0.333 | 72 / 24 | 0 |
| standard-bulk | lapse-20 | boundary | solo − team | +0.417 | 72 / 24 | 0 |
| standard-bulk | lapse-20 | teammate | solo-seam − team | +0.208 | 72 / 24 | 0 |
| standard-bulk | lapse-20 | protocol | hint − team | +0.208 | 72 / 24 | 0 |
| standard-bulk | lapse-30 | boundary | solo − team | +0.875 | 72 / 24 | 0 |
| standard-bulk | lapse-30 | teammate | solo-seam − team | +0.250 | 72 / 24 | 0 |
| standard-bulk | lapse-30 | protocol | hint − team | +0.250 | 72 / 24 | 0 |
| standard-bulk | lapse-50 | boundary | solo − team | +0.917 | 72 / 24 | 0 |
| standard-bulk | lapse-50 | teammate | solo-seam − team | +0.250 | 72 / 24 | 0 |
| standard-bulk | lapse-50 | protocol | hint − team | +0.250 | 72 / 24 | 0 |
| heavy-bulk | careful | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| heavy-bulk | careful | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy-bulk | careful | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy-bulk | naive | boundary | solo − team | +1.000 | 72 / 24 | 0 |
| heavy-bulk | naive | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy-bulk | naive | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy-bulk | nop | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| heavy-bulk | nop | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy-bulk | nop | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy-bulk | blind | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| heavy-bulk | blind | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy-bulk | blind | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy-bulk | random | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| heavy-bulk | random | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy-bulk | random | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy-bulk | lone | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| heavy-bulk | lone | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy-bulk | lone | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy-bulk | script-loop | boundary | solo − team | +1.000 | 72 / 24 | 0 |
| heavy-bulk | script-loop | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy-bulk | script-loop | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy-bulk | script-filter | boundary | solo − team | +1.000 | 72 / 24 | 0 |
| heavy-bulk | script-filter | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy-bulk | script-filter | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy-bulk | script-check | boundary | solo − team | +0.000 | 72 / 24 | 0 |
| heavy-bulk | script-check | teammate | solo-seam − team | +0.000 | 72 / 24 | 0 |
| heavy-bulk | script-check | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| heavy-bulk | lapse-10 | boundary | solo − team | +0.417 | 72 / 24 | 0 |
| heavy-bulk | lapse-10 | teammate | solo-seam − team | +0.167 | 72 / 24 | 0 |
| heavy-bulk | lapse-10 | protocol | hint − team | +0.167 | 72 / 24 | 0 |
| heavy-bulk | lapse-20 | boundary | solo − team | +0.792 | 72 / 24 | 0 |
| heavy-bulk | lapse-20 | teammate | solo-seam − team | +0.333 | 72 / 24 | 0 |
| heavy-bulk | lapse-20 | protocol | hint − team | +0.333 | 72 / 24 | 0 |
| heavy-bulk | lapse-30 | boundary | solo − team | +0.917 | 72 / 24 | 0 |
| heavy-bulk | lapse-30 | teammate | solo-seam − team | +0.292 | 72 / 24 | 0 |
| heavy-bulk | lapse-30 | protocol | hint − team | +0.292 | 72 / 24 | 0 |
| heavy-bulk | lapse-50 | boundary | solo − team | +0.958 | 72 / 24 | 0 |
| heavy-bulk | lapse-50 | teammate | solo-seam − team | +0.083 | 72 / 24 | 0 |
| heavy-bulk | lapse-50 | protocol | hint − team | +0.083 | 72 / 24 | 0 |

## Power plan (scripted stand-ins)

Fixed sample, exact two-sided McNemar, α=0.05, target power 0.8; binary success means reward = 1. The paired reward effect also includes partial credit.
Schedule repeats estimate outcome probabilities; they are not independent instances. The planned n assumes independent new instances, one paired run each.
No-effect and NA cells have no planned n. Plans search up to 1,000 instances. These are sensitivity estimates, not model performance predictions.

| Level | Policy | Control − treatment | Reward effect | Success effect | + / − | Valid pairs / instances | Excluded | Planned n |
|---|---|---|---|---|---|---|---|---|
| canary | script-loop | probe-control − probe-trap | +1.000 | +1.000 | 72 / 0 | 72 / 24 | 0 | 6 |
| canary | script-loop | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| canary | lapse-10 | probe-control − probe-trap | +0.083 | +0.083 | 6 / 0 | 72 / 24 | 0 | 94 |
| canary | lapse-10 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| canary | lapse-20 | probe-control − probe-trap | +0.375 | +0.375 | 27 / 0 | 72 / 24 | 0 | 20 |
| canary | lapse-20 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| canary | lapse-30 | probe-control − probe-trap | +0.292 | +0.292 | 21 / 0 | 72 / 24 | 0 | 26 |
| canary | lapse-30 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| canary | lapse-50 | probe-control − probe-trap | +0.500 | +0.500 | 36 / 0 | 72 / 24 | 0 | 15 |
| canary | lapse-50 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| standard | script-loop | probe-control − probe-trap | +1.000 | +1.000 | 72 / 0 | 72 / 24 | 0 | 6 |
| standard | script-loop | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| standard | lapse-10 | probe-control − probe-trap | +0.292 | +0.292 | 21 / 0 | 72 / 24 | 0 | 26 |
| standard | lapse-10 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| standard | lapse-20 | probe-control − probe-trap | +0.208 | +0.208 | 15 / 0 | 72 / 24 | 0 | 37 |
| standard | lapse-20 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| standard | lapse-30 | probe-control − probe-trap | +0.625 | +0.625 | 45 / 0 | 72 / 24 | 0 | 11 |
| standard | lapse-30 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| standard | lapse-50 | probe-control − probe-trap | +0.667 | +0.667 | 48 / 0 | 72 / 24 | 0 | 11 |
| standard | lapse-50 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| heavy | script-loop | probe-control − probe-trap | +1.000 | +1.000 | 72 / 0 | 72 / 24 | 0 | 6 |
| heavy | script-loop | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| heavy | lapse-10 | probe-control − probe-trap | +0.250 | +0.250 | 18 / 0 | 72 / 24 | 0 | 31 |
| heavy | lapse-10 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| heavy | lapse-20 | probe-control − probe-trap | +0.458 | +0.458 | 33 / 0 | 72 / 24 | 0 | 16 |
| heavy | lapse-20 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| heavy | lapse-30 | probe-control − probe-trap | +0.625 | +0.625 | 45 / 0 | 72 / 24 | 0 | 11 |
| heavy | lapse-30 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| heavy | lapse-50 | probe-control − probe-trap | +0.875 | +0.875 | 63 / 0 | 72 / 24 | 0 | 8 |
| heavy | lapse-50 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| standard-bulk | script-loop | probe-control − probe-trap | +1.000 | +1.000 | 72 / 0 | 72 / 24 | 0 | 6 |
| standard-bulk | script-loop | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| standard-bulk | lapse-10 | probe-control − probe-trap | +0.292 | +0.292 | 21 / 0 | 72 / 24 | 0 | 26 |
| standard-bulk | lapse-10 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| standard-bulk | lapse-20 | probe-control − probe-trap | +0.208 | +0.208 | 15 / 0 | 72 / 24 | 0 | 37 |
| standard-bulk | lapse-20 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| standard-bulk | lapse-30 | probe-control − probe-trap | +0.625 | +0.625 | 45 / 0 | 72 / 24 | 0 | 11 |
| standard-bulk | lapse-30 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| standard-bulk | lapse-50 | probe-control − probe-trap | +0.667 | +0.667 | 48 / 0 | 72 / 24 | 0 | 11 |
| standard-bulk | lapse-50 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| heavy-bulk | script-loop | probe-control − probe-trap | +1.000 | +1.000 | 72 / 0 | 72 / 24 | 0 | 6 |
| heavy-bulk | script-loop | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| heavy-bulk | lapse-10 | probe-control − probe-trap | +0.250 | +0.250 | 18 / 0 | 72 / 24 | 0 | 31 |
| heavy-bulk | lapse-10 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| heavy-bulk | lapse-20 | probe-control − probe-trap | +0.458 | +0.458 | 33 / 0 | 72 / 24 | 0 | 16 |
| heavy-bulk | lapse-20 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| heavy-bulk | lapse-30 | probe-control − probe-trap | +0.625 | +0.625 | 45 / 0 | 72 / 24 | 0 | 11 |
| heavy-bulk | lapse-30 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |
| heavy-bulk | lapse-50 | probe-control − probe-trap | +0.875 | +0.875 | 63 / 0 | 72 / 24 | 0 | 8 |
| heavy-bulk | lapse-50 | team-fullrecord − team | NA | NA | 0 / 0 | 0 / 0 | 72 | NA |

## Versions

| Task(s) | Simulator versions |
|---|---|
| canary/solo/0 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "a8861848a414"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo-seam/0 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "9f2dd3f38195"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/team/0 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "b671632470e1", "p2": "7f3de9569754"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/probe-control/0, canary/probe-quiet/0, canary/probe-trap/0 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "b671632470e1", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/hint/0 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "4c613fcc2047", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo/1 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "ecc19b13ae77"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo-seam/1 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "b29d75d5d12d"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/team/1 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "3a3a58694f7b", "p2": "c5f8a659d2c9"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/probe-control/1, canary/probe-quiet/1, canary/probe-trap/1 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "3a3a58694f7b", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/hint/1 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "622445096afa", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo/2 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "c31d2d7701e2"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo-seam/2 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "65e79b1fdc07"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/team/2 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "f0f2967509f7", "p2": "436c93f0ced6"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/probe-control/2, canary/probe-quiet/2, canary/probe-trap/2 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "f0f2967509f7", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/hint/2 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "5652761f4596", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo/3 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "68ea7abb4d1d"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo-seam/3 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "56db523983e0"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/team/3 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e5e0b74664db", "p2": "ad21d121321e"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/probe-control/3, canary/probe-quiet/3, canary/probe-trap/3 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e5e0b74664db", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/hint/3 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "daf4e7b2173a", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo/4 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "2ac482f92d76"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo-seam/4 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "9b9da71384a2"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/team/4 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "97d53cfe72be", "p2": "22e6802e18c1"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/probe-control/4, canary/probe-quiet/4, canary/probe-trap/4 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "97d53cfe72be", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/hint/4 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "58ef1dd815bb", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo/5 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "401a4b7cb4a2"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo-seam/5 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "726873bd5c49"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/team/5 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "2167e9b014c4", "p2": "00a81d75d431"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/probe-control/5, canary/probe-quiet/5, canary/probe-trap/5 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "2167e9b014c4", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/hint/5 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "cadc12e6f8e2", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo/6 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "fbe55fbeca62"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo-seam/6 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e042415116c8"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/team/6 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "23b8635d427c", "p2": "c9abf8fd808a"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/probe-control/6, canary/probe-quiet/6, canary/probe-trap/6 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "23b8635d427c", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/hint/6 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e8b91da4a575", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo/7 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "7f4f798a8cbc"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo-seam/7 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "4f1082a21af7"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/team/7 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "5342b6626694", "p2": "172c45739ff7"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/probe-control/7, canary/probe-quiet/7, canary/probe-trap/7 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "5342b6626694", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/hint/7 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "092c8583209b", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo/8 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "b63e8f7f7f26"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo-seam/8 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "1ffc174cca6f"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/team/8 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "adc807b0e985", "p2": "965c206b27d0"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/probe-control/8, canary/probe-quiet/8, canary/probe-trap/8 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "adc807b0e985", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/hint/8 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "20095ac30da8", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo/9 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d087f7506a35"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo-seam/9 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "688232a5630c"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/team/9 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "65b0eae34f32", "p2": "9aa5d274aeee"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/probe-control/9, canary/probe-quiet/9, canary/probe-trap/9 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "65b0eae34f32", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/hint/9 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e75f47bc3355", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo/10 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "1fd3f9215ac0"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo-seam/10 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "91a24b7257ac"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/team/10 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "3dfcddcbbbaf", "p2": "2c520f634d19"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/probe-control/10, canary/probe-quiet/10, canary/probe-trap/10 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "3dfcddcbbbaf", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/hint/10 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "3d153ddedf39", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo/11 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "30f155b9caf8"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo-seam/11 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "3c51febc46fa"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/team/11 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "4ea03ad17deb", "p2": "cb97232f35a6"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/probe-control/11, canary/probe-quiet/11, canary/probe-trap/11 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "4ea03ad17deb", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/hint/11 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "046f1dd69e7b", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo/12 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "637e2cf8bdb4"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo-seam/12 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "8f5a63e5d5fd"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/team/12 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "90affe6d7c9a", "p2": "b8d6eee02d69"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/probe-control/12, canary/probe-quiet/12, canary/probe-trap/12 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "90affe6d7c9a", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/hint/12 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "2620b165a45b", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo/13 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "a5f678796dcc"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo-seam/13 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "8060fdabc976"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/team/13 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "5a85b7bce64e", "p2": "f99373a111a3"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/probe-control/13, canary/probe-quiet/13, canary/probe-trap/13 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "5a85b7bce64e", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/hint/13 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "7a871a2c6c34", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo/14 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d628e5f22df5"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo-seam/14 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "636a39cbebdb"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/team/14 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "b5065b7ba36e", "p2": "2c45df8030c8"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/probe-control/14, canary/probe-quiet/14, canary/probe-trap/14 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "b5065b7ba36e", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/hint/14 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "471a8b943be0", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo/15 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "c7eda8bf3ae2"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo-seam/15 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "574bcdb8425f"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/team/15 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "7ef148f7017b", "p2": "c9abf8fd808a"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/probe-control/15, canary/probe-quiet/15, canary/probe-trap/15 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "7ef148f7017b", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/hint/15 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "f1e80e70bc6f", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo/16 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "3d3f2125956d"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo-seam/16 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "b30c7dfec1b2"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/team/16 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d73b12764d80", "p2": "19ac58a32a9e"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/probe-control/16, canary/probe-quiet/16, canary/probe-trap/16 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d73b12764d80", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/hint/16 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "76370506b70f", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo/17 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "8db09e5abe09"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo-seam/17 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "69181c7d6625"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/team/17 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "94bf3cf43bb0", "p2": "cdb2853f4e75"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/probe-control/17, canary/probe-quiet/17, canary/probe-trap/17 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "94bf3cf43bb0", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/hint/17 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "6f677ac68acc", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo/18 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "a43d45840fb0"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo-seam/18 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "a747b5bd925e"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/team/18 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "3c88586e01be", "p2": "534076d917f9"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/probe-control/18, canary/probe-quiet/18, canary/probe-trap/18 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "3c88586e01be", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/hint/18 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "f8f364e9e013", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo/19 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "14d6d9f29eba"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo-seam/19 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "346d709cda00"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/team/19 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "fbcccfc2061d", "p2": "8b331fbd342a"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/probe-control/19, canary/probe-quiet/19, canary/probe-trap/19 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "fbcccfc2061d", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/hint/19 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "1ce34c99674b", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo/20 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "6dc491b9dfce"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo-seam/20 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "9fe44a3f5145"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/team/20 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "36c06b0af66a", "p2": "cb97232f35a6"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/probe-control/20, canary/probe-quiet/20, canary/probe-trap/20 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "36c06b0af66a", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/hint/20 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "035549153b56", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo/21 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "603c04f90b99"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo-seam/21 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "8c74d2797554"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/team/21 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "38bab3d4b386", "p2": "1485e371bc60"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/probe-control/21, canary/probe-quiet/21, canary/probe-trap/21 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "38bab3d4b386", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/hint/21 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e70353cd9d47", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo/22 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "83dfdb735247"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo-seam/22 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "0dc2e9b74366"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/team/22 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "a5e749c75cad", "p2": "2c45df8030c8"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/probe-control/22, canary/probe-quiet/22, canary/probe-trap/22 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "a5e749c75cad", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/hint/22 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "eb080dbab44f", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo/23 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "df25cf1e8f2b"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/solo-seam/23 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "aef5ea1f922b"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/team/23 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "fe3011236f9b", "p2": "6f64ac49a83b"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/probe-control/23, canary/probe-quiet/23, canary/probe-trap/23 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "fe3011236f9b", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| canary/hint/23 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e985cc725b22", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo/0 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "affc85fe869a"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo-seam/0 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "6703715ce325"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/team/0 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "0d5f04a5284d", "p2": "80702ecee818"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/probe-control/0, standard/probe-quiet/0, standard/probe-trap/0 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "0d5f04a5284d", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/hint/0 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "8a7146703185", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo/1 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e2f45836e1ca"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo-seam/1 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "622737e039e4"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/team/1 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "5c7a22d7b782", "p2": "34be776b1bc2"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/probe-control/1, standard/probe-quiet/1, standard/probe-trap/1 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "5c7a22d7b782", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/hint/1 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "dcba6048a763", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo/2 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "006bd94067cc"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo-seam/2 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "5c0d651cc598"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/team/2 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "891449c90895", "p2": "33f9c9d74531"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/probe-control/2, standard/probe-quiet/2, standard/probe-trap/2 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "891449c90895", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/hint/2 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "2acb4c8a84e3", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo/3 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "1c4d076ab479"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo-seam/3 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "7a1beca6b177"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/team/3 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "2ad747db6824", "p2": "5b2639475a00"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/probe-control/3, standard/probe-quiet/3, standard/probe-trap/3 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "2ad747db6824", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/hint/3 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "38f2f4a4b211", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo/4 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "c0b25656bca5"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo-seam/4 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "c9f7435bd7c0"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/team/4 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "2d7029ecd3a4", "p2": "5cca9e226024"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/probe-control/4, standard/probe-quiet/4, standard/probe-trap/4 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "2d7029ecd3a4", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/hint/4 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "9d247e878d9e", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo/5 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "2581aadeedb8"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo-seam/5 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "392b714b68fd"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/team/5 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "541f3a22e26e", "p2": "38cb83796326"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/probe-control/5, standard/probe-quiet/5, standard/probe-trap/5 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "541f3a22e26e", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/hint/5 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e27552b84d93", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo/6 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "0e38ab0f10de"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo-seam/6 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "0ff22848cad9"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/team/6 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e121d3d14440", "p2": "6197c6847678"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/probe-control/6, standard/probe-quiet/6, standard/probe-trap/6 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e121d3d14440", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/hint/6 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "2b271fe40486", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo/7 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "1c529945af19"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo-seam/7 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "c3d9b6a5643f"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/team/7 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "c1619c6e6a21", "p2": "66d82a55ba81"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/probe-control/7, standard/probe-quiet/7, standard/probe-trap/7 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "c1619c6e6a21", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/hint/7 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "b8762cec41e9", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo/8 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "ea20300f5ed6"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo-seam/8 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "58fe7f43f152"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/team/8 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "33ecbf64d766", "p2": "bc5f474849c1"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/probe-control/8, standard/probe-quiet/8, standard/probe-trap/8 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "33ecbf64d766", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/hint/8 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "56a0329214a5", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo/9 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "824659c1b3c5"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo-seam/9 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "b3d78b031641"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/team/9 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "084fe330628f", "p2": "dac82031df9a"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/probe-control/9, standard/probe-quiet/9, standard/probe-trap/9 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "084fe330628f", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/hint/9 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "820ff41c0a79", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo/10 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d8b67baf9aab"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo-seam/10 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "3e2a17b9e10d"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/team/10 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "ee3c0d5bd838", "p2": "0fecd7c40374"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/probe-control/10, standard/probe-quiet/10, standard/probe-trap/10 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "ee3c0d5bd838", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/hint/10 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d33025f8a148", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo/11 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "5409aafe04b3"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo-seam/11 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "8c54c0d0a9c2"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/team/11 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "f8e33e4063cf", "p2": "717547060040"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/probe-control/11, standard/probe-quiet/11, standard/probe-trap/11 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "f8e33e4063cf", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/hint/11 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "f8713b8aff3e", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo/12 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "a6e46ac00bca"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo-seam/12 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "8dd37a3685e5"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/team/12 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "96bfb18472b6", "p2": "9245fd06351d"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/probe-control/12, standard/probe-quiet/12, standard/probe-trap/12 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "96bfb18472b6", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/hint/12 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "91b784ebb32c", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo/13 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "1abd05e0edae"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo-seam/13 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "90b4a6652e95"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/team/13 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "3346db871ba3", "p2": "128bdbb751a5"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/probe-control/13, standard/probe-quiet/13, standard/probe-trap/13 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "3346db871ba3", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/hint/13 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "6261f7fde9fa", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo/14 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "fc217e95dcf5"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo-seam/14 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "abe573a7962c"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/team/14 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "81a283882e56", "p2": "d9f91d2427ab"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/probe-control/14, standard/probe-quiet/14, standard/probe-trap/14 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "81a283882e56", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/hint/14 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "78af0a80f88a", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo/15 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "1d625cbc210f"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo-seam/15 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "c2e701da414e"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/team/15 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "958926cc8536", "p2": "fc83940bb67d"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/probe-control/15, standard/probe-quiet/15, standard/probe-trap/15 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "958926cc8536", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/hint/15 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "9ed57b44a7d6", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo/16 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "c582f20c19ab"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo-seam/16 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "b13a908c2633"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/team/16 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "3204a79cebcf", "p2": "931a5988b727"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/probe-control/16, standard/probe-quiet/16, standard/probe-trap/16 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "3204a79cebcf", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/hint/16 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "bee21b553583", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo/17 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "af894e4a31aa"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo-seam/17 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "138b4b67b4d8"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/team/17 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "1cc83bb8c491", "p2": "be4283aee48f"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/probe-control/17, standard/probe-quiet/17, standard/probe-trap/17 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "1cc83bb8c491", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/hint/17 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "b95c7d78c28e", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo/18 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "5d376229bde7"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo-seam/18 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "02233f92dbef"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/team/18 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e023b403f973", "p2": "a3b3d0c226f1"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/probe-control/18, standard/probe-quiet/18, standard/probe-trap/18 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e023b403f973", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/hint/18 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "023bbc080b25", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo/19 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "fc1b60a518eb"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo-seam/19 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "7aa76356e566"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/team/19 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "bbc273ad0438", "p2": "fd9cc742e203"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/probe-control/19, standard/probe-quiet/19, standard/probe-trap/19 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "bbc273ad0438", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/hint/19 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d3eaccb2814b", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo/20 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "5452be92dec2"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo-seam/20 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "ce458066fd14"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/team/20 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "0b361c966953", "p2": "7e8ac7db9321"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/probe-control/20, standard/probe-quiet/20, standard/probe-trap/20 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "0b361c966953", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/hint/20 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "20e113818f54", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo/21 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "f35229866ca5"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo-seam/21 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d494112d7364"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/team/21 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "311620174bf0", "p2": "377fe1833cdd"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/probe-control/21, standard/probe-quiet/21, standard/probe-trap/21 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "311620174bf0", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/hint/21 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "3abd4e04dab7", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo/22 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "278bbc974dbb"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo-seam/22 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "594548507e69"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/team/22 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e85e2e7db816", "p2": "e5ff8780b5e7"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/probe-control/22, standard/probe-quiet/22, standard/probe-trap/22 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e85e2e7db816", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/hint/22 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "deff06709020", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo/23 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "ac35a18a592b"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/solo-seam/23 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d917fc524b6d"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/team/23 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "4b9e5e1d56eb", "p2": "d9910425b03d"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/probe-control/23, standard/probe-quiet/23, standard/probe-trap/23 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "4b9e5e1d56eb", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard/hint/23 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "fe8c55d6edf3", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo/0 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "303fde1e4e74"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo-seam/0 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "f0d0cf1b4a4d"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/team/0 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "24e010dbca7b", "p2": "5bdf6b004788"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/probe-control/0, heavy/probe-quiet/0, heavy/probe-trap/0 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "24e010dbca7b", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/hint/0 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "f963430fdf6d", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo/1 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "ce0e48be86b7"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo-seam/1 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d68d52eed473"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/team/1 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "3335bd83295e", "p2": "668716e872b2"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/probe-control/1, heavy/probe-quiet/1, heavy/probe-trap/1 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "3335bd83295e", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/hint/1 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "efcfbabdcb6e", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo/2 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "4efb95e79be7"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo-seam/2 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "95fd865db158"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/team/2 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "254f8984d259", "p2": "d34271682bfc"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/probe-control/2, heavy/probe-quiet/2, heavy/probe-trap/2 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "254f8984d259", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/hint/2 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "a90c517395b6", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo/3 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d51815c1b46a"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo-seam/3 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "98880352dd39"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/team/3 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "3c766ab4befe", "p2": "4477560cc01b"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/probe-control/3, heavy/probe-quiet/3, heavy/probe-trap/3 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "3c766ab4befe", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/hint/3 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "267fef60ee83", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo/4 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "15dad872e0a8"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo-seam/4 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "5f6e46a0e9ca"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/team/4 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "6bc503d8f543", "p2": "e8412337ed91"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/probe-control/4, heavy/probe-quiet/4, heavy/probe-trap/4 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "6bc503d8f543", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/hint/4 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "cad877250fd8", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo/5 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "6472a9934fd1"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo-seam/5 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "2bf7a44d6e79"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/team/5 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "77774a739216", "p2": "5bbf3fa4e57d"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/probe-control/5, heavy/probe-quiet/5, heavy/probe-trap/5 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "77774a739216", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/hint/5 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "633a6419a9e7", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo/6 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "3ed60997d364"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo-seam/6 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "4c438f616e9e"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/team/6 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d6a224a51820", "p2": "f83b8a326283"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/probe-control/6, heavy/probe-quiet/6, heavy/probe-trap/6 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d6a224a51820", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/hint/6 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "17a1771fd65e", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo/7 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "6b9cb0910737"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo-seam/7 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "1a532923e574"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/team/7 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "4443b9c55267", "p2": "c8ce46c01ff3"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/probe-control/7, heavy/probe-quiet/7, heavy/probe-trap/7 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "4443b9c55267", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/hint/7 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "58afc9f0949f", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo/8 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d3b587195afe"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo-seam/8 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "2e8fcf6f0539"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/team/8 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "9f769fe9acb1", "p2": "3d013ff883fc"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/probe-control/8, heavy/probe-quiet/8, heavy/probe-trap/8 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "9f769fe9acb1", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/hint/8 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "f13a63d30050", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo/9 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "71eeb787d364"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo-seam/9 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "711da269a005"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/team/9 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "c0a056b1d5e8", "p2": "80ea31912d2c"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/probe-control/9, heavy/probe-quiet/9, heavy/probe-trap/9 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "c0a056b1d5e8", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/hint/9 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "49ea0992d6ae", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo/10 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "0d9e98ba7444"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo-seam/10 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "adcc2870fa12"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/team/10 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "4727361ca2e5", "p2": "4fb8e19a5c5f"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/probe-control/10, heavy/probe-quiet/10, heavy/probe-trap/10 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "4727361ca2e5", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/hint/10 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "60dccadea800", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo/11 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "734e00e79aa4"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo-seam/11 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "ece05f4c63c1"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/team/11 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "b05a9b1cfc27", "p2": "a27b62094992"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/probe-control/11, heavy/probe-quiet/11, heavy/probe-trap/11 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "b05a9b1cfc27", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/hint/11 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "1b98deac9054", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo/12 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "ff320d3dd4d6"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo-seam/12 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "4c7eca21fe32"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/team/12 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "abe6e8e35232", "p2": "d79f8b2cd7a0"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/probe-control/12, heavy/probe-quiet/12, heavy/probe-trap/12 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "abe6e8e35232", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/hint/12 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "ee3403cb09fe", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo/13 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "16c84be61bc0"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo-seam/13 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "16ccd1caf3fa"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/team/13 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "7ab1facae420", "p2": "32b01afd0792"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/probe-control/13, heavy/probe-quiet/13, heavy/probe-trap/13 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "7ab1facae420", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/hint/13 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "93c68cbc6363", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo/14 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "487b3a322092"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo-seam/14 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "433c549646b3"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/team/14 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "92afdc2e393b", "p2": "9943f390e44b"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/probe-control/14, heavy/probe-quiet/14, heavy/probe-trap/14 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "92afdc2e393b", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/hint/14 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "84a345dcafc3", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo/15 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "c1b8b70b8b71"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo-seam/15 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d0cfb9bfe765"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/team/15 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "ecfec867288f", "p2": "ec5cadef88d3"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/probe-control/15, heavy/probe-quiet/15, heavy/probe-trap/15 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "ecfec867288f", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/hint/15 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "157ad322c082", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo/16 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "b9c1b1e23591"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo-seam/16 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "861d84cf5166"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/team/16 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d569f8a3d19d", "p2": "667c1bcbb5a6"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/probe-control/16, heavy/probe-quiet/16, heavy/probe-trap/16 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d569f8a3d19d", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/hint/16 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "89454c95178a", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo/17 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "934bfeb91ee6"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo-seam/17 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "bd7b68fb3015"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/team/17 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d7b4aa4c1316", "p2": "f8000eff951b"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/probe-control/17, heavy/probe-quiet/17, heavy/probe-trap/17 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d7b4aa4c1316", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/hint/17 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "2169861f267b", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo/18 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "dfced55a8898"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo-seam/18 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "cb8c330f9521"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/team/18 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "613880bdeeed", "p2": "46059428da3e"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/probe-control/18, heavy/probe-quiet/18, heavy/probe-trap/18 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "613880bdeeed", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/hint/18 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "137285a9d670", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo/19 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d7fefd5a0055"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo-seam/19 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "9eb762655099"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/team/19 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "58f42a1f7997", "p2": "e4b0c51007b3"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/probe-control/19, heavy/probe-quiet/19, heavy/probe-trap/19 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "58f42a1f7997", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/hint/19 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "0bc54b6ade58", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo/20 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e458656995aa"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo-seam/20 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "f90948f40be5"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/team/20 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e6241f296ba4", "p2": "bf443e847906"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/probe-control/20, heavy/probe-quiet/20, heavy/probe-trap/20 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e6241f296ba4", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/hint/20 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "9d5793d08c2c", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo/21 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "61b5232dfd5c"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo-seam/21 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "8ab4d626f72b"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/team/21 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "393f0c8c874c", "p2": "d4fa24ef8d29"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/probe-control/21, heavy/probe-quiet/21, heavy/probe-trap/21 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "393f0c8c874c", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/hint/21 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "759d804c8191", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo/22 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "8b3cbee5b1a3"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo-seam/22 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "f7dde2361cb1"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/team/22 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "6c82333b7b36", "p2": "25abf02e75ac"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/probe-control/22, heavy/probe-quiet/22, heavy/probe-trap/22 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "6c82333b7b36", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/hint/22 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "fba22723ec75", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo/23 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "435fc705281d"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/solo-seam/23 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "55797ae7824d"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/team/23 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "78af5f53f01b", "p2": "2202fbfc86db"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/probe-control/23, heavy/probe-quiet/23, heavy/probe-trap/23 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "78af5f53f01b", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy/hint/23 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "b1f6b39428d1", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo/0 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "9caf55b40f53"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo-seam/0 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e2f21b344a7c"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/team/0 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "0a499b494c15", "p2": "c200c43c8eb3"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/probe-control/0, standard-bulk/probe-quiet/0, standard-bulk/probe-trap/0 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "0a499b494c15", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/hint/0 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "348ca114fdf3", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo/1 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e84f01a9c1a1"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo-seam/1 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "4bde4cd66950"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/team/1 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "2de96a78d4c7", "p2": "c529291a715f"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/probe-control/1, standard-bulk/probe-quiet/1, standard-bulk/probe-trap/1 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "2de96a78d4c7", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/hint/1 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "0d852589327d", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo/2 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "50bcdf7ebb32"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo-seam/2 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "5e7317d656c1"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/team/2 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "fe20f1e69d67", "p2": "37a5de28b892"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/probe-control/2, standard-bulk/probe-quiet/2, standard-bulk/probe-trap/2 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "fe20f1e69d67", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/hint/2 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "18417fbb30c9", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo/3 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "55f5180dec35"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo-seam/3 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "327bd587be2f"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/team/3 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "0ccc720d7ffd", "p2": "3f37a4c89837"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/probe-control/3, standard-bulk/probe-quiet/3, standard-bulk/probe-trap/3 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "0ccc720d7ffd", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/hint/3 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "236bac155281", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo/4 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "c02fa06347be"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo-seam/4 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "54fe76f3e0a5"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/team/4 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "70b9964dce3b", "p2": "65e43bb60de0"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/probe-control/4, standard-bulk/probe-quiet/4, standard-bulk/probe-trap/4 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "70b9964dce3b", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/hint/4 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "5f1a2d736a5b", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo/5 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "a1971c6ed1b0"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo-seam/5 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "51abdff03160"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/team/5 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d73ebb0a0c3f", "p2": "fb7748a9cf49"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/probe-control/5, standard-bulk/probe-quiet/5, standard-bulk/probe-trap/5 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d73ebb0a0c3f", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/hint/5 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "3ffe3896f723", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo/6 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "546ce54e8a37"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo-seam/6 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "a16f3a960e78"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/team/6 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "0420fdbb88bf", "p2": "138548430376"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/probe-control/6, standard-bulk/probe-quiet/6, standard-bulk/probe-trap/6 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "0420fdbb88bf", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/hint/6 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "bb2c0a277b67", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo/7 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "71544ad0ad26"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo-seam/7 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "6ceef0443371"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/team/7 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "965e034084f3", "p2": "da1fceff45b2"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/probe-control/7, standard-bulk/probe-quiet/7, standard-bulk/probe-trap/7 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "965e034084f3", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/hint/7 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "a3be0aaafe42", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo/8 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "9124b0840915"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo-seam/8 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "73ebf8bcbdea"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/team/8 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "ca84cce67e14", "p2": "157ce22de681"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/probe-control/8, standard-bulk/probe-quiet/8, standard-bulk/probe-trap/8 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "ca84cce67e14", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/hint/8 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e88d655a340f", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo/9 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "cd9e9a2447e6"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo-seam/9 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "fe957bc250ef"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/team/9 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "57603fc0b101", "p2": "37df9510b34c"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/probe-control/9, standard-bulk/probe-quiet/9, standard-bulk/probe-trap/9 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "57603fc0b101", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/hint/9 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "7e763f688da2", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo/10 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "86044a334fea"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo-seam/10 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e4bd41fd71cb"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/team/10 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "a46c0decc065", "p2": "02cef8c62e81"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/probe-control/10, standard-bulk/probe-quiet/10, standard-bulk/probe-trap/10 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "a46c0decc065", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/hint/10 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "61448553a0bb", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo/11 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "fdadb5fcd66a"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo-seam/11 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "2ea8adfbd6ef"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/team/11 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e96f4d91db40", "p2": "68e624a67422"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/probe-control/11, standard-bulk/probe-quiet/11, standard-bulk/probe-trap/11 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e96f4d91db40", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/hint/11 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "cf600b47a4f6", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo/12 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "2a8111d24350"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo-seam/12 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "417c3922f52a"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/team/12 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "3f3489d962e1", "p2": "0f44e4b8bb81"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/probe-control/12, standard-bulk/probe-quiet/12, standard-bulk/probe-trap/12 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "3f3489d962e1", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/hint/12 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "05205aa56561", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo/13 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "c19962e37598"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo-seam/13 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "c37c267e9f66"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/team/13 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "aae93662c414", "p2": "bb7c6b0a181e"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/probe-control/13, standard-bulk/probe-quiet/13, standard-bulk/probe-trap/13 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "aae93662c414", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/hint/13 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "682436ea406a", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo/14 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "c3d48fa000b4"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo-seam/14 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "488c2f1345de"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/team/14 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "7ff012c4a846", "p2": "d41478f01b39"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/probe-control/14, standard-bulk/probe-quiet/14, standard-bulk/probe-trap/14 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "7ff012c4a846", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/hint/14 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "dae370135ddf", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo/15 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "a855f10a97a1"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo-seam/15 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "30ef23109cca"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/team/15 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "3c054e3b45c3", "p2": "511e3e9db903"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/probe-control/15, standard-bulk/probe-quiet/15, standard-bulk/probe-trap/15 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "3c054e3b45c3", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/hint/15 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "6a698b965070", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo/16 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "a1e4f013274f"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo-seam/16 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "7ab12b06f36c"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/team/16 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "6768cbae65e3", "p2": "8641efa7306f"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/probe-control/16, standard-bulk/probe-quiet/16, standard-bulk/probe-trap/16 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "6768cbae65e3", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/hint/16 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "9b906efd12c5", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo/17 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "7506a3acd407"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo-seam/17 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "f32f39e503b9"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/team/17 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e36535754354", "p2": "851d61fea91d"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/probe-control/17, standard-bulk/probe-quiet/17, standard-bulk/probe-trap/17 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e36535754354", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/hint/17 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "f7b73a9a6322", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo/18 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "8a89702290c4"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo-seam/18 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "ad6cc15faeab"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/team/18 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "7d8618cad9a4", "p2": "920486c6a200"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/probe-control/18, standard-bulk/probe-quiet/18, standard-bulk/probe-trap/18 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "7d8618cad9a4", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/hint/18 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "bd7b0e588aa3", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo/19 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "79ba6b9a66b2"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo-seam/19 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "001e773b3ef9"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/team/19 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "1aa38e4dbb25", "p2": "7e03210d740f"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/probe-control/19, standard-bulk/probe-quiet/19, standard-bulk/probe-trap/19 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "1aa38e4dbb25", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/hint/19 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "5b1bc8de9740", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo/20 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "60332efa6df1"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo-seam/20 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "100487fd9556"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/team/20 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d6bb456ece44", "p2": "e7e07fc1aa31"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/probe-control/20, standard-bulk/probe-quiet/20, standard-bulk/probe-trap/20 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d6bb456ece44", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/hint/20 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "4505d3790ee0", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo/21 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "dc32a32dd8ba"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo-seam/21 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "ef7e22f51822"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/team/21 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "590d3caf9a02", "p2": "09e0a5c5da4b"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/probe-control/21, standard-bulk/probe-quiet/21, standard-bulk/probe-trap/21 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "590d3caf9a02", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/hint/21 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "ac764fda9300", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo/22 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "bac0641cdc26"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo-seam/22 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d2f5b3cab35b"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/team/22 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "70df1ae6faf7", "p2": "8826d6a7a312"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/probe-control/22, standard-bulk/probe-quiet/22, standard-bulk/probe-trap/22 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "70df1ae6faf7", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/hint/22 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "8384a1f41d0c", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo/23 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "4703eb908daa"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/solo-seam/23 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "817480e47890"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/team/23 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "524f7f9c455d", "p2": "3443a29bb38e"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/probe-control/23, standard-bulk/probe-quiet/23, standard-bulk/probe-trap/23 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "524f7f9c455d", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| standard-bulk/hint/23 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "8047fcd795ea", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo/0 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "97b2a2dfd879"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo-seam/0 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "f2b2f00ae26e"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/team/0 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "056036b7897b", "p2": "3cb51ee9503e"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/probe-control/0, heavy-bulk/probe-quiet/0, heavy-bulk/probe-trap/0 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "056036b7897b", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/hint/0 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e13b5af99da1", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo/1 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "79cc1532523b"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo-seam/1 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "db73fecfe12d"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/team/1 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d7cefe01eee2", "p2": "9ead5e08b971"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/probe-control/1, heavy-bulk/probe-quiet/1, heavy-bulk/probe-trap/1 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d7cefe01eee2", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/hint/1 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "da834898388d", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo/2 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e564291b3f3c"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo-seam/2 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "c9af3d6c8e45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/team/2 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "4f758603f706", "p2": "e23047032204"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/probe-control/2, heavy-bulk/probe-quiet/2, heavy-bulk/probe-trap/2 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "4f758603f706", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/hint/2 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "37cb4fd2eaf3", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo/3 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "abf701328302"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo-seam/3 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "28d24fe54274"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/team/3 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e0e7a306ec16", "p2": "c0075cb62c4a"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/probe-control/3, heavy-bulk/probe-quiet/3, heavy-bulk/probe-trap/3 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e0e7a306ec16", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/hint/3 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "8234eacd2a02", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo/4 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "c07b112776f6"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo-seam/4 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "38a0ba3fbbc1"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/team/4 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "2f61649ddf93", "p2": "29d438b36092"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/probe-control/4, heavy-bulk/probe-quiet/4, heavy-bulk/probe-trap/4 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "2f61649ddf93", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/hint/4 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "a50f7cee25de", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo/5 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "f5b1a0fe7214"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo-seam/5 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "ae60c1e58469"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/team/5 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "8478321d8b66", "p2": "a9ea523a72dd"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/probe-control/5, heavy-bulk/probe-quiet/5, heavy-bulk/probe-trap/5 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "8478321d8b66", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/hint/5 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "762797766163", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo/6 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "7258948d2153"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo-seam/6 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "cbec63cb6684"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/team/6 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "a003738eb8dd", "p2": "c50b53dd1b05"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/probe-control/6, heavy-bulk/probe-quiet/6, heavy-bulk/probe-trap/6 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "a003738eb8dd", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/hint/6 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "23cacacb4d94", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo/7 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d0ae29697c1e"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo-seam/7 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "042ecdd223a6"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/team/7 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "569d83afe78a", "p2": "8fb7b4bbba0c"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/probe-control/7, heavy-bulk/probe-quiet/7, heavy-bulk/probe-trap/7 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "569d83afe78a", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/hint/7 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "4be0e07f5356", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo/8 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "adf5a1c3b0f4"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo-seam/8 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "998b305d27a0"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/team/8 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "57df42e338c9", "p2": "0347a1a2806c"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/probe-control/8, heavy-bulk/probe-quiet/8, heavy-bulk/probe-trap/8 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "57df42e338c9", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/hint/8 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "fbd0eebdefb8", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo/9 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "ea50cd754395"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo-seam/9 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "5bc6c826fd4b"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/team/9 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "843d06c163a0", "p2": "bbd07e29e28a"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/probe-control/9, heavy-bulk/probe-quiet/9, heavy-bulk/probe-trap/9 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "843d06c163a0", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/hint/9 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "2355712d396d", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo/10 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "324fbdb2bd52"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo-seam/10 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "db4bd7bf8784"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/team/10 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "0a9bc9e6a257", "p2": "5bd79bb6e089"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/probe-control/10, heavy-bulk/probe-quiet/10, heavy-bulk/probe-trap/10 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "0a9bc9e6a257", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/hint/10 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "6f5993924cc8", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo/11 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "0d1e97000d88"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo-seam/11 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "baa2045372ab"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/team/11 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "add760b3529c", "p2": "7d4465f37528"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/probe-control/11, heavy-bulk/probe-quiet/11, heavy-bulk/probe-trap/11 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "add760b3529c", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/hint/11 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "839664152a25", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo/12 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "39e3fbd42190"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo-seam/12 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "63d715e50e06"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/team/12 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "0f43fff93dbf", "p2": "4cd561973b7b"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/probe-control/12, heavy-bulk/probe-quiet/12, heavy-bulk/probe-trap/12 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "0f43fff93dbf", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/hint/12 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "7b4a53f54ecb", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo/13 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "571a4d0bb421"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo-seam/13 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "a3cea18a573e"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/team/13 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "c7631dcdf765", "p2": "bc6bbe8fb74e"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/probe-control/13, heavy-bulk/probe-quiet/13, heavy-bulk/probe-trap/13 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "c7631dcdf765", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/hint/13 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "ddc844a49267", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo/14 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "47f64491c89e"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo-seam/14 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "0fb7b996038f"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/team/14 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "cd419ed66164", "p2": "8ad4c1f7900e"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/probe-control/14, heavy-bulk/probe-quiet/14, heavy-bulk/probe-trap/14 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "cd419ed66164", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/hint/14 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "895d26f8a862", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo/15 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "ce25012409ec"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo-seam/15 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "6a874a84ae4f"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/team/15 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "7a6f14d0d00e", "p2": "4748a0951435"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/probe-control/15, heavy-bulk/probe-quiet/15, heavy-bulk/probe-trap/15 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "7a6f14d0d00e", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/hint/15 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "0ebd2005537f", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo/16 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "53c0bcf2fab4"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo-seam/16 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "a989923d877d"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/team/16 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d7ac365dc04e", "p2": "d925d52a391c"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/probe-control/16, heavy-bulk/probe-quiet/16, heavy-bulk/probe-trap/16 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d7ac365dc04e", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/hint/16 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "c78c7beb8908", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo/17 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "cb447caee560"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo-seam/17 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "6aa839e9bac4"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/team/17 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "7afdf2f615ec", "p2": "49c4466178d8"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/probe-control/17, heavy-bulk/probe-quiet/17, heavy-bulk/probe-trap/17 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "7afdf2f615ec", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/hint/17 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "4a1248a84f12", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo/18 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "ab889b06296b"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo-seam/18 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "eb1bae76625d"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/team/18 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "50e8f6b04392", "p2": "57fd741a006b"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/probe-control/18, heavy-bulk/probe-quiet/18, heavy-bulk/probe-trap/18 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "50e8f6b04392", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/hint/18 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "55f8f26da490", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo/19 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "baeaa889575a"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo-seam/19 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e44e647344be"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/team/19 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "ad50d6d356f2", "p2": "70dfd1074024"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/probe-control/19, heavy-bulk/probe-quiet/19, heavy-bulk/probe-trap/19 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "ad50d6d356f2", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/hint/19 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "07149ad3755b", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo/20 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e8871e61dd4f"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo-seam/20 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "a10b24dff9e5"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/team/20 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "2cf7032f52b0", "p2": "17a288de273d"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/probe-control/20, heavy-bulk/probe-quiet/20, heavy-bulk/probe-trap/20 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "2cf7032f52b0", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/hint/20 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "d8c43284192f", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo/21 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "e49b2c9e1c6d"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo-seam/21 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "77d5c8c9cbe0"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/team/21 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "c4b256f547b1", "p2": "16fa7a0c0f74"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/probe-control/21, heavy-bulk/probe-quiet/21, heavy-bulk/probe-trap/21 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "c4b256f547b1", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/hint/21 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "1f3d7d1c7797", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo/22 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "ffe75cc3ece3"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo-seam/22 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "129d9b9be113"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/team/22 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "ecf7d4a6df9d", "p2": "e2fa5e7b34b4"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/probe-control/22, heavy-bulk/probe-quiet/22, heavy-bulk/probe-trap/22 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "ecf7d4a6df9d", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/hint/22 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "6caf074dca94", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo/23 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "f40bed881353"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/solo-seam/23 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "f9fbedfe4023"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/team/23 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "6b1996000d8c", "p2": "4d52c494ed86"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/probe-control/23, heavy-bulk/probe-quiet/23, heavy-bulk/probe-trap/23 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "6b1996000d8c", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |
| heavy-bulk/hint/23 | `{"extension": "f3715d83172b", "family": "242f1462627d", "family_version": 4, "instructions": {"p1": "1cee19c15649", "p2": "905bf5f95b45"}, "policies": "846052ab4a1b", "policy_runner": "5722b6365ad1", "world": "d561f4c4bb50"}` |

## Load and cost (estimates)

Estimate: 4 characters per input token; repeat the instruction, JSON briefing and seat files once per simulated world request (at least once per model seat). Excludes scripted seats, system prompts, replies, history and output tokens. Requests are not measured model turns.

| Level / arm | Per-seat max calls / briefing chars | Mean estimated tokens | $1 / $5 / $15 per M |
|---|---|---|---|
| canary / solo | p1: 31/695 | 28021 | $0.02802 / $0.14011 / $0.42032 |
| canary / solo-seam | p1: 33/570 | 33348 | $0.03335 / $0.16674 / $0.50022 |
| canary / team | p1: 33/570, p2: 1/83 | 33689 | $0.03369 / $0.16844 / $0.50533 |
| canary / probe-control | p1: 33/570, p2: 0/34 | 33341 | $0.03334 / $0.16670 / $0.50011 |
| canary / probe-trap | p1: 33/570, p2: 0/34 | 33341 | $0.03334 / $0.16670 / $0.50011 |
| canary / hint | p1: 33/570, p2: 0/34 | 34100 | $0.03410 / $0.17050 / $0.51150 |
| canary / probe-quiet | p1: 33/570, p2: 0/34 | 33341 | $0.03334 / $0.16670 / $0.50011 |
| standard / solo | p1: 31/813 | 29843 | $0.02984 / $0.14921 / $0.44764 |
| standard / solo-seam | p1: 33/569 | 33418 | $0.03342 / $0.16709 / $0.50127 |
| standard / team | p1: 33/569, p2: 1/131 | 33801 | $0.03380 / $0.16900 / $0.50701 |
| standard / probe-control | p1: 33/569, p2: 0/34 | 33414 | $0.03341 / $0.16707 / $0.50121 |
| standard / probe-trap | p1: 33/569, p2: 0/34 | 33414 | $0.03341 / $0.16707 / $0.50121 |
| standard / hint | p1: 33/569, p2: 0/34 | 34173 | $0.03417 / $0.17086 / $0.51259 |
| standard / probe-quiet | p1: 33/569, p2: 0/34 | 33414 | $0.03341 / $0.16707 / $0.50121 |
| heavy / solo | p1: 31/924 | 31555 | $0.03156 / $0.15778 / $0.47333 |
| heavy / solo-seam | p1: 33/568 | 33366 | $0.03337 / $0.16683 / $0.50049 |
| heavy / team | p1: 33/568, p2: 1/179 | 33786 | $0.03379 / $0.16893 / $0.50679 |
| heavy / probe-control | p1: 33/568, p2: 0/34 | 33359 | $0.03336 / $0.16679 / $0.50038 |
| heavy / probe-trap | p1: 33/568, p2: 0/34 | 33359 | $0.03336 / $0.16679 / $0.50038 |
| heavy / hint | p1: 33/568, p2: 0/34 | 34118 | $0.03412 / $0.17059 / $0.51177 |
| heavy / probe-quiet | p1: 33/568, p2: 0/34 | 33359 | $0.03336 / $0.16679 / $0.50038 |
| standard-bulk / solo | p1: 103/813 | 99155 | $0.09915 / $0.49577 / $1.48732 |
| standard-bulk / solo-seam | p1: 105/569 | 106330 | $0.10633 / $0.53165 / $1.59495 |
| standard-bulk / team | p1: 105/569, p2: 1/131 | 106704 | $0.10670 / $0.53352 / $1.60055 |
| standard-bulk / probe-control | p1: 105/569, p2: 0/34 | 106317 | $0.10632 / $0.53158 / $1.59475 |
| standard-bulk / probe-trap | p1: 105/569, p2: 0/34 | 106317 | $0.10632 / $0.53158 / $1.59475 |
| standard-bulk / hint | p1: 105/569, p2: 0/34 | 108732 | $0.10873 / $0.54366 / $1.63098 |
| standard-bulk / probe-quiet | p1: 105/569, p2: 0/34 | 106317 | $0.10632 / $0.53158 / $1.59475 |
| heavy-bulk / solo | p1: 199/924 | 202565 | $0.20257 / $1.01283 / $3.03848 |
| heavy-bulk / solo-seam | p1: 201/568 | 203228 | $0.20323 / $1.01614 / $3.04842 |
| heavy-bulk / team | p1: 201/568, p2: 1/179 | 203612 | $0.20361 / $1.01806 / $3.05419 |
| heavy-bulk / probe-control | p1: 201/568, p2: 0/34 | 203186 | $0.20319 / $1.01593 / $3.04779 |
| heavy-bulk / probe-trap | p1: 201/568, p2: 0/34 | 203186 | $0.20319 / $1.01593 / $3.04779 |
| heavy-bulk / hint | p1: 201/568, p2: 0/34 | 207809 | $0.20781 / $1.03904 / $3.11713 |
| heavy-bulk / probe-quiet | p1: 201/568, p2: 0/34 | 203186 | $0.20319 / $1.01593 / $3.04779 |

## Evidence and predictions

[flotilla/docs/seams/commit.md](file:///Users/petermakhnatch/Developer/flotilla-wt/seams-commit/flotilla/docs/seams/commit.md)

## Open risks

- None flagged.

Scripted policy results are not estimates of frontier-model performance.
