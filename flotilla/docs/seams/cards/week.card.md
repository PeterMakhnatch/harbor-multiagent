# EvalCard: week — Dispatch week at Kestrel Freight and Fulfilment

**Seam:** Four context boundaries inside one dispatch workflow; integration demonstration, not an isolated seam.
**Kit commit:** `5b6a7ba2d58c4bd98460fe221298d393b0bfee39`; variant: `default`.
**Arms:** solo, team, team-fullrecord, hint. Levels: canary (canary), standard (standard).
**Sweep:** 24 instances × 3 schedule seeds. No model calls.
**Policies unavailable:** blind, lone, script-loop.

## Policy matrix (mean / min / n; invalid count in parentheses)

| Level | Arm | Seats | careful | naive | nop | random | lapse-10 | lapse-20 | lapse-30 | lapse-50 |
|---|---|---|---|---|---|---|---|---|---|---|
| canary | solo | a | 1.000 / 1.000 / 72 (0) | 0.504 / 0.405 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.802 / 0.702 / 72 (0) | 0.876 / 0.405 / 72 (0) | 0.888 / 0.702 / 72 (0) | 0.826 / 0.107 / 72 (0) | 0.636 / 0.000 / 72 (0) |
| canary | team | a, n1, n2, r | 1.000 / 1.000 / 72 (0) | 0.069 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.421 / 0.321 / 72 (0) | 0.828 / 0.071 / 72 (0) | 0.720 / 0.083 / 72 (0) | 0.599 / 0.000 / 72 (0) | 0.368 / 0.000 / 72 (0) |
| canary | team-fullrecord | a, n1, n2, r | 1.000 / 1.000 / 72 (0) | 0.123 / 0.024 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.421 / 0.321 / 72 (0) | 0.830 / 0.071 / 72 (0) | 0.750 / 0.321 / 72 (0) | 0.688 / 0.107 / 72 (0) | 0.458 / 0.000 / 72 (0) |
| canary | hint | a, n1, n2, r | 1.000 / 1.000 / 72 (0) | 0.069 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.421 / 0.321 / 72 (0) | 0.828 / 0.071 / 72 (0) | 0.720 / 0.083 / 72 (0) | 0.599 / 0.000 / 72 (0) | 0.368 / 0.000 / 72 (0) |
| standard | solo | a | 1.000 / 1.000 / 72 (0) | 0.659 / 0.603 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.810 / 0.762 / 72 (0) | 0.947 / 0.762 / 72 (0) | 0.884 / 0.524 / 72 (0) | 0.810 / 0.643 / 72 (0) | 0.727 / 0.524 / 72 (0) |
| standard | team | a, n1, n2, r, s | 1.000 / 1.000 / 72 (0) | 0.121 / 0.041 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.429 / 0.381 / 72 (0) | 0.886 / 0.579 / 72 (0) | 0.759 / 0.387 / 72 (0) | 0.618 / 0.302 / 72 (0) | 0.404 / 0.002 / 72 (0) |
| standard | team-fullrecord | a, n1, n2, r, s | 1.000 / 1.000 / 72 (0) | 0.278 / 0.222 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.429 / 0.381 / 72 (0) | 0.899 / 0.583 / 72 (0) | 0.799 / 0.425 / 72 (0) | 0.670 / 0.340 / 72 (0) | 0.501 / 0.110 / 72 (0) |
| standard | hint | a, n1, n2, r, s | 1.000 / 1.000 / 72 (0) | 0.121 / 0.041 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.429 / 0.381 / 72 (0) | 0.886 / 0.579 / 72 (0) | 0.759 / 0.387 / 72 (0) | 0.618 / 0.302 / 72 (0) | 0.404 / 0.002 / 72 (0) |

## Checks

| Check | Standard | Result | Evidence |
|---|---|---|---|
| levels | A5 | **PASS** | All requested dose levels available |
| solvable | A2/B1 | **PASS** | 576 runs; careful reward = 1 |
| easy alone | A2 | **PASS** | 144 runs; solo careful reward = 1 |
| floor | A3 | **PASS** | 576 runs; every nop reward ≤ 0.05 |
| chance | A3 | **PASS** | canary/team/random: actual 0.42063492063492064, expected 0.3958 (absolute tolerance 0.1); standard/team/random: actual 0.4289021164021164, expected 0.4405 (absolute tolerance 0.1) |
| separation | A4 | **NA** | No trap/control arms |
| dose response | A5/I1 | **NA** | Needs naive team at canary and heavy |
| one seat is not enough | A3/C1 | **NA** | No applicable policy/arm/level; every lone team reward ≤ 0.5 |
| schedule independence | E3 | **PASS** | 576 runs; careful scores 1 across 3 schedule seed(s); max within-instance naive spread: 0.0 |
| simulation validity | F1 | **PASS** | 4608 runs; No invalid runs in the ordinary sweep |
| determinism | E1/E2 | **PASS** | 16/16 sample event hashes match |
| no leak | C1/D2 | **PASS** | 192 built tasks; seat views and family QA scanned |
| pairs | A1/D2 | **PASS** | team→hint: 48 pairs, max 571 changed chars; fields: a/instruction, n1/instruction, n2/instruction, r/instruction, s/instruction; team→team-fullrecord: 48 pairs, max 135 changed chars; fields: n2/instruction |
| twin rule | A1 | **PASS** | Every team/trap has a declared twin |
| invalid is not zero | F1 | **FAIL** | Budget-limited simulation: stalled=False, invalid=False, reward=0.0 |
| canary | A6 | **PASS** | Marker checked in 192 task files and all seat views |
| power plan | A3 | **PASS** | 8 paired comparisons; fixed exact McNemar plan (see table) |

## Three costs (paired reward differences)

Positive means the control scores higher than team; missing arms are NA.

| Level | Policy | Cost | Control − team | Effect | Valid pairs / instances | Excluded |
|---|---|---|---|---|---|---|
| canary | careful | boundary | team-fullrecord − team | +0.000 | 72 / 24 | 0 |
| canary | careful | teammate | solo-seam − team | NA | 0 / 0 | 72 |
| canary | careful | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | naive | boundary | team-fullrecord − team | +0.054 | 72 / 24 | 0 |
| canary | naive | teammate | solo-seam − team | NA | 0 / 0 | 72 |
| canary | naive | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | nop | boundary | team-fullrecord − team | +0.000 | 72 / 24 | 0 |
| canary | nop | teammate | solo-seam − team | NA | 0 / 0 | 72 |
| canary | nop | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | random | boundary | team-fullrecord − team | +0.000 | 72 / 24 | 0 |
| canary | random | teammate | solo-seam − team | NA | 0 / 0 | 72 |
| canary | random | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-10 | boundary | team-fullrecord − team | +0.002 | 72 / 24 | 0 |
| canary | lapse-10 | teammate | solo-seam − team | NA | 0 / 0 | 72 |
| canary | lapse-10 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-20 | boundary | team-fullrecord − team | +0.030 | 72 / 24 | 0 |
| canary | lapse-20 | teammate | solo-seam − team | NA | 0 / 0 | 72 |
| canary | lapse-20 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-30 | boundary | team-fullrecord − team | +0.089 | 72 / 24 | 0 |
| canary | lapse-30 | teammate | solo-seam − team | NA | 0 / 0 | 72 |
| canary | lapse-30 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-50 | boundary | team-fullrecord − team | +0.090 | 72 / 24 | 0 |
| canary | lapse-50 | teammate | solo-seam − team | NA | 0 / 0 | 72 |
| canary | lapse-50 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | careful | boundary | team-fullrecord − team | +0.000 | 72 / 24 | 0 |
| standard | careful | teammate | solo-seam − team | NA | 0 / 0 | 72 |
| standard | careful | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | naive | boundary | team-fullrecord − team | +0.157 | 72 / 24 | 0 |
| standard | naive | teammate | solo-seam − team | NA | 0 / 0 | 72 |
| standard | naive | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | nop | boundary | team-fullrecord − team | +0.000 | 72 / 24 | 0 |
| standard | nop | teammate | solo-seam − team | NA | 0 / 0 | 72 |
| standard | nop | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | random | boundary | team-fullrecord − team | +0.000 | 72 / 24 | 0 |
| standard | random | teammate | solo-seam − team | NA | 0 / 0 | 72 |
| standard | random | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-10 | boundary | team-fullrecord − team | +0.014 | 72 / 24 | 0 |
| standard | lapse-10 | teammate | solo-seam − team | NA | 0 / 0 | 72 |
| standard | lapse-10 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-20 | boundary | team-fullrecord − team | +0.040 | 72 / 24 | 0 |
| standard | lapse-20 | teammate | solo-seam − team | NA | 0 / 0 | 72 |
| standard | lapse-20 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-30 | boundary | team-fullrecord − team | +0.052 | 72 / 24 | 0 |
| standard | lapse-30 | teammate | solo-seam − team | NA | 0 / 0 | 72 |
| standard | lapse-30 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-50 | boundary | team-fullrecord − team | +0.096 | 72 / 24 | 0 |
| standard | lapse-50 | teammate | solo-seam − team | NA | 0 / 0 | 72 |
| standard | lapse-50 | protocol | hint − team | +0.000 | 72 / 24 | 0 |

## Power plan (scripted stand-ins)

Fixed sample, exact two-sided McNemar, α=0.05, target power 0.8; binary success means reward = 1. The paired reward effect also includes partial credit.
Schedule repeats estimate outcome probabilities; they are not independent instances. The planned n assumes independent new instances, one paired run each.
No-effect and NA cells have no planned n. Plans search up to 1,000 instances. These are sensitivity estimates, not model performance predictions.

| Level | Policy | Control − treatment | Reward effect | Success effect | + / − | Valid pairs / instances | Excluded | Planned n |
|---|---|---|---|---|---|---|---|---|
| canary | lapse-10 | team-fullrecord − team | +0.002 | +0.042 | 3 / 0 | 72 / 24 | 0 | 189 |
| canary | lapse-20 | team-fullrecord − team | +0.030 | +0.083 | 6 / 0 | 72 / 24 | 0 | 94 |
| canary | lapse-30 | team-fullrecord − team | +0.089 | +0.000 | 0 / 0 | 72 / 24 | 0 | no effect |
| canary | lapse-50 | team-fullrecord − team | +0.090 | +0.000 | 0 / 0 | 72 / 24 | 0 | no effect |
| standard | lapse-10 | team-fullrecord − team | +0.014 | +0.000 | 0 / 0 | 72 / 24 | 0 | no effect |
| standard | lapse-20 | team-fullrecord − team | +0.040 | +0.000 | 0 / 0 | 72 / 24 | 0 | no effect |
| standard | lapse-30 | team-fullrecord − team | +0.052 | +0.000 | 0 / 0 | 72 / 24 | 0 | no effect |
| standard | lapse-50 | team-fullrecord − team | +0.096 | +0.000 | 0 / 0 | 72 / 24 | 0 | no effect |

## Versions

| Task(s) | Simulator versions |
|---|---|
| canary/solo/0, canary/solo/10, canary/solo/14, canary/solo/16, canary/solo/19, canary/solo/2, canary/solo/20, canary/solo/21, canary/solo/22, canary/solo/23, canary/solo/4, canary/solo/6, canary/solo/7, canary/solo/8, canary/solo/9 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "e1819c4df02e"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/team/0, canary/team/21, canary/team/4 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "c916b0070fab", "n1": "90f3c28d29cb", "n2": "ec8308bd1da8", "r": "31452b09325f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/team-fullrecord/0, canary/team-fullrecord/21, canary/team-fullrecord/4 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "c916b0070fab", "n1": "90f3c28d29cb", "n2": "14cf7163219b", "r": "31452b09325f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/hint/0, canary/hint/21, canary/hint/4 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "0d6d052953c5", "n1": "4939ae13c7c8", "n2": "e34ed5763aec", "r": "e7018234250c"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/solo/1, canary/solo/13, canary/solo/18 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "07d4a5f532ae"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/team/1 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "8b0290b90af8", "n1": "90f3c28d29cb", "n2": "5680398ff293", "r": "31452b09325f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/team-fullrecord/1 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "8b0290b90af8", "n1": "90f3c28d29cb", "n2": "84a86837ba31", "r": "31452b09325f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/hint/1 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "cb182537760f", "n1": "4939ae13c7c8", "n2": "e9592dc0d0e3", "r": "e7018234250c"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/team/10, canary/team/14, canary/team/19, canary/team/2, canary/team/23, canary/team/7, canary/team/9 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "c916b0070fab", "n1": "90f3c28d29cb", "n2": "5680398ff293", "r": "31452b09325f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/team-fullrecord/10, canary/team-fullrecord/14, canary/team-fullrecord/19, canary/team-fullrecord/2, canary/team-fullrecord/23, canary/team-fullrecord/7, canary/team-fullrecord/9 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "c916b0070fab", "n1": "90f3c28d29cb", "n2": "84a86837ba31", "r": "31452b09325f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/hint/10, canary/hint/14, canary/hint/19, canary/hint/2, canary/hint/23, canary/hint/7, canary/hint/9 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "0d6d052953c5", "n1": "4939ae13c7c8", "n2": "e9592dc0d0e3", "r": "e7018234250c"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/solo/3 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "278a4facea03"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/team/3 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "d47cef9e0bfe", "n1": "90f3c28d29cb", "n2": "5680398ff293", "r": "31452b09325f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/team-fullrecord/3 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "d47cef9e0bfe", "n1": "90f3c28d29cb", "n2": "84a86837ba31", "r": "31452b09325f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/hint/3 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "8f74dd26333f", "n1": "4939ae13c7c8", "n2": "e9592dc0d0e3", "r": "e7018234250c"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/solo/11, canary/solo/15, canary/solo/5 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "61c1f59d4b71"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/team/11, canary/team/15, canary/team/5 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "755a1dacc3af", "n1": "90f3c28d29cb", "n2": "5680398ff293", "r": "31452b09325f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/team-fullrecord/11, canary/team-fullrecord/15, canary/team-fullrecord/5 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "755a1dacc3af", "n1": "90f3c28d29cb", "n2": "84a86837ba31", "r": "31452b09325f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/hint/11, canary/hint/15, canary/hint/5 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "0766127f8482", "n1": "4939ae13c7c8", "n2": "e9592dc0d0e3", "r": "e7018234250c"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/team/20, canary/team/6 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "755a1dacc3af", "n1": "90f3c28d29cb", "n2": "ec8308bd1da8", "r": "31452b09325f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/team-fullrecord/20, canary/team-fullrecord/6 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "755a1dacc3af", "n1": "90f3c28d29cb", "n2": "14cf7163219b", "r": "31452b09325f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/hint/20, canary/hint/6 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "0766127f8482", "n1": "4939ae13c7c8", "n2": "e34ed5763aec", "r": "e7018234250c"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/team/8 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "9b9b78fd8a0a", "n1": "90f3c28d29cb", "n2": "5680398ff293", "r": "31452b09325f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/team-fullrecord/8 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "9b9b78fd8a0a", "n1": "90f3c28d29cb", "n2": "84a86837ba31", "r": "31452b09325f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/hint/8 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "0e519c83e49c", "n1": "4939ae13c7c8", "n2": "e9592dc0d0e3", "r": "e7018234250c"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/solo/12, canary/solo/17 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "4193533e9889"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/team/12 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "fdfc0ba8d457", "n1": "90f3c28d29cb", "n2": "5680398ff293", "r": "31452b09325f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/team-fullrecord/12 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "fdfc0ba8d457", "n1": "90f3c28d29cb", "n2": "84a86837ba31", "r": "31452b09325f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/hint/12 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "6421149d7eb6", "n1": "4939ae13c7c8", "n2": "e9592dc0d0e3", "r": "e7018234250c"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/team/13, canary/team/18 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "8b0290b90af8", "n1": "90f3c28d29cb", "n2": "ec8308bd1da8", "r": "31452b09325f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/team-fullrecord/13, canary/team-fullrecord/18 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "8b0290b90af8", "n1": "90f3c28d29cb", "n2": "14cf7163219b", "r": "31452b09325f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/hint/13, canary/hint/18 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "cb182537760f", "n1": "4939ae13c7c8", "n2": "e34ed5763aec", "r": "e7018234250c"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/team/16 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "9b9b78fd8a0a", "n1": "90f3c28d29cb", "n2": "ec8308bd1da8", "r": "31452b09325f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/team-fullrecord/16 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "9b9b78fd8a0a", "n1": "90f3c28d29cb", "n2": "14cf7163219b", "r": "31452b09325f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/hint/16 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "0e519c83e49c", "n1": "4939ae13c7c8", "n2": "e34ed5763aec", "r": "e7018234250c"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/team/17 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "fdfc0ba8d457", "n1": "90f3c28d29cb", "n2": "ec8308bd1da8", "r": "31452b09325f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/team-fullrecord/17 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "fdfc0ba8d457", "n1": "90f3c28d29cb", "n2": "14cf7163219b", "r": "31452b09325f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/hint/17 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "6421149d7eb6", "n1": "4939ae13c7c8", "n2": "e34ed5763aec", "r": "e7018234250c"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/team/22 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "416602a29087", "n1": "90f3c28d29cb", "n2": "ec8308bd1da8", "r": "31452b09325f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/team-fullrecord/22 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "416602a29087", "n1": "90f3c28d29cb", "n2": "14cf7163219b", "r": "31452b09325f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| canary/hint/22 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "6fd3e7633a07", "n1": "4939ae13c7c8", "n2": "e34ed5763aec", "r": "e7018234250c"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/solo/0 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "c8a41bcdda8a"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team/0 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "9060aea3d399", "n1": "099d7ea25147", "n2": "504263a608cf", "r": "d4cb6f3a771b", "s": "7fae8ea042be"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team-fullrecord/0 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "9060aea3d399", "n1": "099d7ea25147", "n2": "68ea4b4ee072", "r": "d4cb6f3a771b", "s": "7fae8ea042be"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/hint/0 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "417a8318d074", "n1": "9077c5893efd", "n2": "e72609bbc401", "r": "ecec691f21fb", "s": "05f81a5e1beb"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/solo/1 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "2295b3a7655e"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team/1 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "5b949f419b60", "n1": "6ec56ee42244", "n2": "c34d4bba200f", "r": "d4cb6f3a771b", "s": "47e11a10bc57"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team-fullrecord/1 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "5b949f419b60", "n1": "6ec56ee42244", "n2": "cd360f4f0992", "r": "d4cb6f3a771b", "s": "47e11a10bc57"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/hint/1 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "d2e3183f2ea7", "n1": "cd4246dca731", "n2": "7c0b3b303d52", "r": "ecec691f21fb", "s": "5b244dfe293f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/solo/2 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "09b576c3da10"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team/2 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "67f4c275aae0", "n1": "6b2f7a1f9a07", "n2": "d97c63b0db39", "r": "d4cb6f3a771b", "s": "556b37fb7a2e"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team-fullrecord/2 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "67f4c275aae0", "n1": "6b2f7a1f9a07", "n2": "3eccd608e422", "r": "d4cb6f3a771b", "s": "556b37fb7a2e"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/hint/2 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "9a945478f532", "n1": "e80252ff06a4", "n2": "c3c1b2f6ae8a", "r": "ecec691f21fb", "s": "5dc9fd4fdb38"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/solo/3 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "ede46b1b1dc1"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team/3 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "85160d96bfcf", "n1": "5fa76fc8f42f", "n2": "77e9ea469eb4", "r": "d4cb6f3a771b", "s": "9295cad16457"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team-fullrecord/3 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "85160d96bfcf", "n1": "5fa76fc8f42f", "n2": "8956f807d2cd", "r": "d4cb6f3a771b", "s": "9295cad16457"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/hint/3 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "55dc245d1efe", "n1": "851cfcd21ef1", "n2": "00e4fc3ada24", "r": "ecec691f21fb", "s": "f14cfdf7fd8f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/solo/4 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "4db296edb2a3"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team/4 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "618705279307", "n1": "5fa76fc8f42f", "n2": "0b74327a5e97", "r": "d4cb6f3a771b", "s": "5f0713d3556c"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team-fullrecord/4 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "618705279307", "n1": "5fa76fc8f42f", "n2": "8ff81e7e3f6c", "r": "d4cb6f3a771b", "s": "5f0713d3556c"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/hint/4 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "18a50d5c7921", "n1": "851cfcd21ef1", "n2": "0ae50fc0703c", "r": "ecec691f21fb", "s": "0af09b027b4c"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/solo/5 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "a69de386dad8"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team/5 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "74d7930a6c29", "n1": "099d7ea25147", "n2": "9d5ea00b183d", "r": "d4cb6f3a771b", "s": "f9e9a09a25b6"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team-fullrecord/5 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "74d7930a6c29", "n1": "099d7ea25147", "n2": "d6843f70aef7", "r": "d4cb6f3a771b", "s": "f9e9a09a25b6"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/hint/5 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "ee1012a42d3b", "n1": "9077c5893efd", "n2": "621d71449e73", "r": "ecec691f21fb", "s": "8cbc97e8df4e"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/solo/6 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "533ccb37bc09"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team/6 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "7505cc79f6cf", "n1": "1d983b6959b1", "n2": "4b5234a47707", "r": "d4cb6f3a771b", "s": "891dbd4c2bd7"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team-fullrecord/6 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "7505cc79f6cf", "n1": "1d983b6959b1", "n2": "4fac0f6c6a27", "r": "d4cb6f3a771b", "s": "891dbd4c2bd7"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/hint/6 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "cd1a46eca973", "n1": "ceec7aa0695c", "n2": "b38fcd42313c", "r": "ecec691f21fb", "s": "93adb2386db0"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/solo/7 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "4aa2e35c0bd3"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team/7 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "ab31655d21f7", "n1": "7dfbabc8029d", "n2": "4b5234a47707", "r": "d4cb6f3a771b", "s": "f10b152e2d68"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team-fullrecord/7 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "ab31655d21f7", "n1": "7dfbabc8029d", "n2": "4fac0f6c6a27", "r": "d4cb6f3a771b", "s": "f10b152e2d68"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/hint/7 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "62d9430ad53d", "n1": "eb418c0c0a7f", "n2": "b38fcd42313c", "r": "ecec691f21fb", "s": "412a36508fde"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/solo/8 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "d4fdcebd6c8f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team/8 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "39a345bdffe2", "n1": "1d983b6959b1", "n2": "78981841c349", "r": "d4cb6f3a771b", "s": "891dbd4c2bd7"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team-fullrecord/8 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "39a345bdffe2", "n1": "1d983b6959b1", "n2": "d370ebe4a686", "r": "d4cb6f3a771b", "s": "891dbd4c2bd7"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/hint/8 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "6478d9d30623", "n1": "ceec7aa0695c", "n2": "e93e01deca6a", "r": "ecec691f21fb", "s": "93adb2386db0"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/solo/9 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "147cfa9539f3"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team/9 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "68b4d0cbbe4e", "n1": "5fa76fc8f42f", "n2": "77e9ea469eb4", "r": "d4cb6f3a771b", "s": "c2d1fd8021fa"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team-fullrecord/9 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "68b4d0cbbe4e", "n1": "5fa76fc8f42f", "n2": "8956f807d2cd", "r": "d4cb6f3a771b", "s": "c2d1fd8021fa"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/hint/9 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "e60a5cee87ca", "n1": "851cfcd21ef1", "n2": "00e4fc3ada24", "r": "ecec691f21fb", "s": "9203c47d4191"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/solo/10 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "b0ffc50f1d95"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team/10 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "7711e88f634a", "n1": "5fa76fc8f42f", "n2": "4b5234a47707", "r": "d4cb6f3a771b", "s": "dfe6f31bc39e"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team-fullrecord/10 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "7711e88f634a", "n1": "5fa76fc8f42f", "n2": "4fac0f6c6a27", "r": "d4cb6f3a771b", "s": "dfe6f31bc39e"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/hint/10 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "d5c298ef7130", "n1": "851cfcd21ef1", "n2": "b38fcd42313c", "r": "ecec691f21fb", "s": "e2eccf4036ae"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/solo/11 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "a960403cd662"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team/11 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "9756d747004e", "n1": "afddfd181d2b", "n2": "4b5234a47707", "r": "d4cb6f3a771b", "s": "d2b4b64efbe8"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team-fullrecord/11 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "9756d747004e", "n1": "afddfd181d2b", "n2": "4fac0f6c6a27", "r": "d4cb6f3a771b", "s": "d2b4b64efbe8"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/hint/11 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "3a2c1175ba18", "n1": "d92244e69ecc", "n2": "b38fcd42313c", "r": "ecec691f21fb", "s": "39372f6227e6"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/solo/12 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "edb0a9aa215d"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team/12 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "dae72c6456a8", "n1": "1d983b6959b1", "n2": "4b5234a47707", "r": "d4cb6f3a771b", "s": "6675f9de2696"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team-fullrecord/12 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "dae72c6456a8", "n1": "1d983b6959b1", "n2": "4fac0f6c6a27", "r": "d4cb6f3a771b", "s": "6675f9de2696"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/hint/12 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "f0582a92ca75", "n1": "ceec7aa0695c", "n2": "b38fcd42313c", "r": "ecec691f21fb", "s": "aad4fb489693"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/solo/13 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "5f083268e1af"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team/13 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "ab31655d21f7", "n1": "5fa76fc8f42f", "n2": "d517c78c3b29", "r": "d4cb6f3a771b", "s": "b301018ef2cb"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team-fullrecord/13 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "ab31655d21f7", "n1": "5fa76fc8f42f", "n2": "92f60ef027d2", "r": "d4cb6f3a771b", "s": "b301018ef2cb"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/hint/13 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "62d9430ad53d", "n1": "851cfcd21ef1", "n2": "eb43b8b1d012", "r": "ecec691f21fb", "s": "af369c058385"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/solo/14 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "e2494c420b83"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team/14 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "126aca7829d9", "n1": "3af6e3dcc88e", "n2": "ea995eaaf834", "r": "d4cb6f3a771b", "s": "c96585e43e80"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team-fullrecord/14 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "126aca7829d9", "n1": "3af6e3dcc88e", "n2": "4c5e6672726f", "r": "d4cb6f3a771b", "s": "c96585e43e80"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/hint/14 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "518d60c547a1", "n1": "7b1fcc08aa39", "n2": "dcf940ef4be0", "r": "ecec691f21fb", "s": "2d66626b5cf4"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/solo/15 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "12526b7e7f18"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team/15 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "2010c827cd7d", "n1": "5fa76fc8f42f", "n2": "4b5234a47707", "r": "d4cb6f3a771b", "s": "11b9141cccdd"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team-fullrecord/15 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "2010c827cd7d", "n1": "5fa76fc8f42f", "n2": "4fac0f6c6a27", "r": "d4cb6f3a771b", "s": "11b9141cccdd"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/hint/15 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "6961eca24fdb", "n1": "851cfcd21ef1", "n2": "b38fcd42313c", "r": "ecec691f21fb", "s": "60587bf31af7"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/solo/16 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "82330a263120"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team/16 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "ed90cc5a9191", "n1": "1d983b6959b1", "n2": "4b5234a47707", "r": "d4cb6f3a771b", "s": "98f0c21bdee1"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team-fullrecord/16 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "ed90cc5a9191", "n1": "1d983b6959b1", "n2": "4fac0f6c6a27", "r": "d4cb6f3a771b", "s": "98f0c21bdee1"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/hint/16 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "1e85645d55c8", "n1": "ceec7aa0695c", "n2": "b38fcd42313c", "r": "ecec691f21fb", "s": "9f755a8791dd"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/solo/17 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "c9921b6f7855"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team/17 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "27bf47f7e715", "n1": "5fa76fc8f42f", "n2": "4b5234a47707", "r": "d4cb6f3a771b", "s": "31cb091fbaaf"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team-fullrecord/17 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "27bf47f7e715", "n1": "5fa76fc8f42f", "n2": "4fac0f6c6a27", "r": "d4cb6f3a771b", "s": "31cb091fbaaf"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/hint/17 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "8ddb6875ef9d", "n1": "851cfcd21ef1", "n2": "b38fcd42313c", "r": "ecec691f21fb", "s": "bb81e93448b3"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/solo/18 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "e3cea16dc84b"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team/18 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "7b9a7db0d7d3", "n1": "afddfd181d2b", "n2": "77e9ea469eb4", "r": "d4cb6f3a771b", "s": "a26a5ad83c96"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team-fullrecord/18 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "7b9a7db0d7d3", "n1": "afddfd181d2b", "n2": "8956f807d2cd", "r": "d4cb6f3a771b", "s": "a26a5ad83c96"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/hint/18 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "b6754aca58d1", "n1": "d92244e69ecc", "n2": "00e4fc3ada24", "r": "ecec691f21fb", "s": "514dfc5cc8ed"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/solo/19 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "a73e272733ba"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team/19 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "c09deea8f5db", "n1": "1d983b6959b1", "n2": "1c42f6618513", "r": "d4cb6f3a771b", "s": "59b251e63152"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team-fullrecord/19 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "c09deea8f5db", "n1": "1d983b6959b1", "n2": "1e1a64746f21", "r": "d4cb6f3a771b", "s": "59b251e63152"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/hint/19 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "90ffd8337abd", "n1": "ceec7aa0695c", "n2": "4c98d12ca5c6", "r": "ecec691f21fb", "s": "a94e5da65c4e"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/solo/20 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "c5f9e55b2079"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team/20 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "d36f7f33c7ec", "n1": "099d7ea25147", "n2": "504263a608cf", "r": "d4cb6f3a771b", "s": "514c508497d5"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team-fullrecord/20 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "d36f7f33c7ec", "n1": "099d7ea25147", "n2": "68ea4b4ee072", "r": "d4cb6f3a771b", "s": "514c508497d5"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/hint/20 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "cea5001839d5", "n1": "9077c5893efd", "n2": "e72609bbc401", "r": "ecec691f21fb", "s": "0ba165ad7718"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/solo/21 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "748eea814c82"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team/21 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "30ab5bbc2271", "n1": "099d7ea25147", "n2": "77e9ea469eb4", "r": "d4cb6f3a771b", "s": "b301018ef2cb"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team-fullrecord/21 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "30ab5bbc2271", "n1": "099d7ea25147", "n2": "8956f807d2cd", "r": "d4cb6f3a771b", "s": "b301018ef2cb"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/hint/21 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "24079d3d13ab", "n1": "9077c5893efd", "n2": "00e4fc3ada24", "r": "ecec691f21fb", "s": "af369c058385"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/solo/22 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "84c54581c294"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team/22 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "14b4c3c5d3e1", "n1": "afddfd181d2b", "n2": "4b5234a47707", "r": "d4cb6f3a771b", "s": "69fd8b0a47ec"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team-fullrecord/22 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "14b4c3c5d3e1", "n1": "afddfd181d2b", "n2": "4fac0f6c6a27", "r": "d4cb6f3a771b", "s": "69fd8b0a47ec"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/hint/22 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "55f37ea48e02", "n1": "d92244e69ecc", "n2": "b38fcd42313c", "r": "ecec691f21fb", "s": "bc13f91b637b"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/solo/23 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "11e066bc2c41"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team/23 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "91cfebfd3504", "n1": "099d7ea25147", "n2": "ea995eaaf834", "r": "d4cb6f3a771b", "s": "b020caa33b03"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/team-fullrecord/23 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "91cfebfd3504", "n1": "099d7ea25147", "n2": "4c5e6672726f", "r": "d4cb6f3a771b", "s": "b020caa33b03"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |
| standard/hint/23 | `{"extension": "48d40df5fe0f", "family": "3fe10777a0a7", "family_version": 2, "instructions": {"a": "9607ba2f4532", "n1": "9077c5893efd", "n2": "dcf940ef4be0", "r": "ecec691f21fb", "s": "07da1b51a3d6"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7978c9452083"}` |

## Load and cost (estimates)

Estimate: 4 characters per input token; repeat the instruction, JSON briefing and seat files once per simulated world request (at least once per model seat). Excludes scripted seats, system prompts, replies, history and output tokens. Requests are not measured model turns.

| Level / arm | Per-seat max calls / briefing chars | Mean estimated tokens | $1 / $5 / $15 per M |
|---|---|---|---|
| canary / solo | a: 29/136 | 36527 | $0.03653 / $0.18263 / $0.54790 |
| canary / team | a: 15/119, n1: 9/111, n2: 16/119, r: 3/104 | 39986 | $0.03999 / $0.19993 / $0.59979 |
| canary / team-fullrecord | a: 15/119, n1: 9/111, n2: 16/119, r: 3/104 | 40049 | $0.04005 / $0.20025 / $0.60074 |
| canary / hint | a: 15/119, n1: 9/111, n2: 16/119, r: 3/104 | 41237 | $0.04124 / $0.20618 / $0.61855 |
| standard / solo | a: 128/271 | 166275 | $0.16628 / $0.83138 / $2.49413 |
| standard / team | a: 59/173, n1: 51/171, n2: 43/162, r: 3/108, s: 78/206 | 231053 | $0.23105 / $1.15526 / $3.46579 |
| standard / team-fullrecord | a: 59/173, n1: 51/171, n2: 43/162, r: 3/108, s: 78/206 | 231254 | $0.23125 / $1.15627 / $3.46881 |
| standard / hint | a: 59/173, n1: 51/171, n2: 43/162, r: 3/108, s: 78/206 | 237616 | $0.23762 / $1.18808 / $3.56424 |

## Evidence and predictions

[flotilla/docs/seams/week.md](file:///Users/petermakhnatch/Developer/flotilla-wt/seams-week2/flotilla/docs/seams/week.md)

## Open risks

- NA: separation
- NA: dose response
- NA: one seat is not enough
- FAIL: invalid is not zero

Scripted policy results are not estimates of frontier-model performance.
