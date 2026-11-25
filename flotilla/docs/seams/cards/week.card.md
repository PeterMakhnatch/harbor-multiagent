# EvalCard: week — Dispatch week at Kestrel Freight and Fulfilment

**Seam:** Four context boundaries inside one dispatch workflow; integration demonstration, not an isolated seam.
**Kit commit:** `c5fa3587af182054041168a666b3a647e62297f0`; variant: `default`.
**Arms:** solo, team, team-fullrecord, hint. Levels: canary (canary), standard (standard).
**Sweep:** 24 instances × 3 schedule seeds. No model calls.
**Policies unavailable:** blind, lone, script-loop.

## Policy matrix (mean / min / n; invalid count in parentheses)

| Level | Arm | Seats | careful | naive | nop | random | lapse-10 | lapse-20 | lapse-30 | lapse-50 |
|---|---|---|---|---|---|---|---|---|---|---|
| canary | solo | a | 1.000 / 1.000 / 72 (0) | 0.516 / 0.405 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.802 / 0.702 / 72 (0) | 0.876 / 0.107 / 72 (0) | 0.851 / 0.405 / 72 (0) | 0.777 / 0.405 / 72 (0) | 0.673 / 0.000 / 72 (0) |
| canary | team | a, n1, n2, r | 1.000 / 1.000 / 72 (0) | 0.082 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.421 / 0.321 / 72 (0) | 0.788 / 0.107 / 72 (0) | 0.663 / 0.048 / 72 (0) | 0.511 / 0.000 / 72 (0) | 0.305 / 0.000 / 72 (0) |
| canary | team-fullrecord | a, n1, n2, r | 1.000 / 1.000 / 72 (0) | 0.135 / 0.024 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.421 / 0.321 / 72 (0) | 0.800 / 0.107 / 72 (0) | 0.691 / 0.071 / 72 (0) | 0.604 / 0.024 / 72 (0) | 0.406 / 0.000 / 72 (0) |
| canary | hint | a, n1, n2, r | 1.000 / 1.000 / 72 (0) | 0.082 / 0.000 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.421 / 0.321 / 72 (0) | 0.788 / 0.107 / 72 (0) | 0.663 / 0.048 / 72 (0) | 0.511 / 0.000 / 72 (0) | 0.305 / 0.000 / 72 (0) |
| standard | solo | a | 1.000 / 1.000 / 72 (0) | 0.640 / 0.563 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.818 / 0.762 / 72 (0) | 0.939 / 0.722 / 72 (0) | 0.904 / 0.802 / 72 (0) | 0.831 / 0.683 / 72 (0) | 0.716 / 0.563 / 72 (0) |
| standard | team | a, n1, n2, r, s | 1.000 / 1.000 / 72 (0) | 0.092 / 0.002 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.437 / 0.381 / 72 (0) | 0.865 / 0.530 / 72 (0) | 0.701 / 0.316 / 72 (0) | 0.615 / 0.244 / 72 (0) | 0.397 / 0.027 / 72 (0) |
| standard | team-fullrecord | a, n1, n2, r, s | 1.000 / 1.000 / 72 (0) | 0.259 / 0.183 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.437 / 0.381 / 72 (0) | 0.880 / 0.530 / 72 (0) | 0.747 / 0.421 / 72 (0) | 0.676 / 0.248 / 72 (0) | 0.482 / 0.103 / 72 (0) |
| standard | hint | a, n1, n2, r, s | 1.000 / 1.000 / 72 (0) | 0.092 / 0.002 / 72 (0) | 0.000 / 0.000 / 72 (0) | 0.437 / 0.381 / 72 (0) | 0.865 / 0.530 / 72 (0) | 0.701 / 0.316 / 72 (0) | 0.615 / 0.244 / 72 (0) | 0.397 / 0.027 / 72 (0) |

## Checks

| Check | Standard | Result | Evidence |
|---|---|---|---|
| levels | A5 | **PASS** | All requested dose levels available |
| solvable | A2/B1 | **PASS** | 576 runs; careful reward = 1 |
| easy alone | A2 | **PASS** | 144 runs; solo careful reward = 1 |
| floor | A3 | **PASS** | 576 runs; every nop reward ≤ 0.05 |
| chance | A3 | **PASS** | canary/team/random: actual 0.42063492063492064, expected 0.3958 (absolute tolerance 0.1); standard/team/random: actual 0.43716931216931215, expected 0.4405 (absolute tolerance 0.1) |
| separation | A4 | **NA** | No trap/control arms |
| dose response | A5/I1 | **NA** | Needs naive team at canary and heavy |
| one seat is not enough | A3/C1 | **NA** | No applicable policy/arm/level; every lone team reward ≤ 0.5 |
| schedule independence | E3 | **PASS** | 576 runs; careful scores 1 across 3 schedule seed(s); max within-instance naive spread: 0.0 |
| simulation validity | F1 | **PASS** | 4608 runs; No invalid runs in the ordinary sweep |
| determinism | E1/E2 | **PASS** | 16/16 sample event hashes match |
| no leak | C1/D2 | **PASS** | 192 built tasks; seat views and family QA scanned |
| pairs | A1/D2 | **PASS** | team→hint: 48 pairs, max 571 changed chars; fields: a/instruction, n1/instruction, n2/instruction, r/instruction, s/instruction; team→team-fullrecord: 48 pairs, max 135 changed chars; fields: n2/instruction |
| twin rule | A1 | **PASS** | Every team/trap has a declared twin |
| invalid is not zero | F1 | **PASS** | Budget-limited simulation: stalled=False, invalid=False, reward=0.0, stalled_seats=['a'] (a cut-off play is void or a valid result that names the stalled seats, never an unflagged score) |
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
| canary | lapse-10 | boundary | team-fullrecord − team | +0.012 | 72 / 24 | 0 |
| canary | lapse-10 | teammate | solo-seam − team | NA | 0 / 0 | 72 |
| canary | lapse-10 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-20 | boundary | team-fullrecord − team | +0.028 | 72 / 24 | 0 |
| canary | lapse-20 | teammate | solo-seam − team | NA | 0 / 0 | 72 |
| canary | lapse-20 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-30 | boundary | team-fullrecord − team | +0.093 | 72 / 24 | 0 |
| canary | lapse-30 | teammate | solo-seam − team | NA | 0 / 0 | 72 |
| canary | lapse-30 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| canary | lapse-50 | boundary | team-fullrecord − team | +0.101 | 72 / 24 | 0 |
| canary | lapse-50 | teammate | solo-seam − team | NA | 0 / 0 | 72 |
| canary | lapse-50 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | careful | boundary | team-fullrecord − team | +0.000 | 72 / 24 | 0 |
| standard | careful | teammate | solo-seam − team | NA | 0 / 0 | 72 |
| standard | careful | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | naive | boundary | team-fullrecord − team | +0.166 | 72 / 24 | 0 |
| standard | naive | teammate | solo-seam − team | NA | 0 / 0 | 72 |
| standard | naive | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | nop | boundary | team-fullrecord − team | +0.000 | 72 / 24 | 0 |
| standard | nop | teammate | solo-seam − team | NA | 0 / 0 | 72 |
| standard | nop | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | random | boundary | team-fullrecord − team | +0.000 | 72 / 24 | 0 |
| standard | random | teammate | solo-seam − team | NA | 0 / 0 | 72 |
| standard | random | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-10 | boundary | team-fullrecord − team | +0.015 | 72 / 24 | 0 |
| standard | lapse-10 | teammate | solo-seam − team | NA | 0 / 0 | 72 |
| standard | lapse-10 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-20 | boundary | team-fullrecord − team | +0.046 | 72 / 24 | 0 |
| standard | lapse-20 | teammate | solo-seam − team | NA | 0 / 0 | 72 |
| standard | lapse-20 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-30 | boundary | team-fullrecord − team | +0.061 | 72 / 24 | 0 |
| standard | lapse-30 | teammate | solo-seam − team | NA | 0 / 0 | 72 |
| standard | lapse-30 | protocol | hint − team | +0.000 | 72 / 24 | 0 |
| standard | lapse-50 | boundary | team-fullrecord − team | +0.085 | 72 / 24 | 0 |
| standard | lapse-50 | teammate | solo-seam − team | NA | 0 / 0 | 72 |
| standard | lapse-50 | protocol | hint − team | +0.000 | 72 / 24 | 0 |

## Power plan (scripted stand-ins)

Fixed sample, exact two-sided McNemar, α=0.05, target power 0.8; binary success means reward = 1. The paired reward effect also includes partial credit.
Schedule repeats estimate outcome probabilities; they are not independent instances. The planned n assumes independent new instances, one paired run each.
No-effect and NA cells have no planned n. Plans search up to 1,000 instances. These are sensitivity estimates, not model performance predictions.

| Level | Policy | Control − treatment | Reward effect | Success effect | + / − | Valid pairs / instances | Excluded | Planned n |
|---|---|---|---|---|---|---|---|---|
| canary | lapse-10 | team-fullrecord − team | +0.012 | +0.042 | 3 / 0 | 72 / 24 | 0 | 189 |
| canary | lapse-20 | team-fullrecord − team | +0.028 | +0.042 | 3 / 0 | 72 / 24 | 0 | 189 |
| canary | lapse-30 | team-fullrecord − team | +0.093 | +0.125 | 9 / 0 | 72 / 24 | 0 | 62 |
| canary | lapse-50 | team-fullrecord − team | +0.101 | +0.000 | 0 / 0 | 72 / 24 | 0 | no effect |
| standard | lapse-10 | team-fullrecord − team | +0.015 | +0.000 | 0 / 0 | 72 / 24 | 0 | no effect |
| standard | lapse-20 | team-fullrecord − team | +0.046 | +0.000 | 0 / 0 | 72 / 24 | 0 | no effect |
| standard | lapse-30 | team-fullrecord − team | +0.061 | +0.000 | 0 / 0 | 72 / 24 | 0 | no effect |
| standard | lapse-50 | team-fullrecord − team | +0.085 | +0.000 | 0 / 0 | 72 / 24 | 0 | no effect |

## Versions

| Task(s) | Simulator versions |
|---|---|
| canary/solo/0 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "5b2e3b3a4aac"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| canary/team/0 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "e7f0c589821b", "n1": "ce420cbf9bea", "n2": "f54b7bcc6b0d", "r": "125bc0a228c2"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| canary/team-fullrecord/0 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "e7f0c589821b", "n1": "ce420cbf9bea", "n2": "138501a2471d", "r": "125bc0a228c2"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| canary/hint/0 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "34931229381b", "n1": "20b3e5760122", "n2": "7e092ddded5e", "r": "92b6e15fdae6"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| canary/solo/1, canary/solo/11, canary/solo/12, canary/solo/4, canary/solo/9 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "9a3b78de171a"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| canary/team/1, canary/team/11, canary/team/12, canary/team/4, canary/team/9 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "6ebca790d7f4", "n1": "ce420cbf9bea", "n2": "f54b7bcc6b0d", "r": "125bc0a228c2"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| canary/team-fullrecord/1, canary/team-fullrecord/11, canary/team-fullrecord/12, canary/team-fullrecord/4, canary/team-fullrecord/9 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "6ebca790d7f4", "n1": "ce420cbf9bea", "n2": "138501a2471d", "r": "125bc0a228c2"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| canary/hint/1, canary/hint/11, canary/hint/12, canary/hint/4, canary/hint/9 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "dd12751f3112", "n1": "20b3e5760122", "n2": "7e092ddded5e", "r": "92b6e15fdae6"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| canary/solo/10, canary/solo/13, canary/solo/14, canary/solo/15, canary/solo/16, canary/solo/17, canary/solo/19, canary/solo/2, canary/solo/20, canary/solo/21, canary/solo/22, canary/solo/23, canary/solo/3, canary/solo/5, canary/solo/6, canary/solo/8 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "75b6746bf27b"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| canary/team/13, canary/team/14, canary/team/19, canary/team/2, canary/team/5 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "3bd31a46d3c1", "n1": "ce420cbf9bea", "n2": "a3f6a6cf2241", "r": "125bc0a228c2"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| canary/team-fullrecord/13, canary/team-fullrecord/14, canary/team-fullrecord/19, canary/team-fullrecord/2, canary/team-fullrecord/5 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "3bd31a46d3c1", "n1": "ce420cbf9bea", "n2": "f25f4a28508f", "r": "125bc0a228c2"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| canary/hint/13, canary/hint/14, canary/hint/19, canary/hint/2, canary/hint/5 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "67d8ab7f926c", "n1": "20b3e5760122", "n2": "8a61c8da3e82", "r": "92b6e15fdae6"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| canary/team/3 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "35f0104e258e", "n1": "ce420cbf9bea", "n2": "a3f6a6cf2241", "r": "125bc0a228c2"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| canary/team-fullrecord/3 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "35f0104e258e", "n1": "ce420cbf9bea", "n2": "f25f4a28508f", "r": "125bc0a228c2"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| canary/hint/3 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "7370dc5f8ff1", "n1": "20b3e5760122", "n2": "8a61c8da3e82", "r": "92b6e15fdae6"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| canary/team/16, canary/team/20, canary/team/21, canary/team/23, canary/team/6 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "3bd31a46d3c1", "n1": "ce420cbf9bea", "n2": "f54b7bcc6b0d", "r": "125bc0a228c2"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| canary/team-fullrecord/16, canary/team-fullrecord/20, canary/team-fullrecord/21, canary/team-fullrecord/23, canary/team-fullrecord/6 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "3bd31a46d3c1", "n1": "ce420cbf9bea", "n2": "138501a2471d", "r": "125bc0a228c2"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| canary/hint/16, canary/hint/20, canary/hint/21, canary/hint/23, canary/hint/6 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "67d8ab7f926c", "n1": "20b3e5760122", "n2": "7e092ddded5e", "r": "92b6e15fdae6"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| canary/solo/18, canary/solo/7 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "d1d70cbe0283"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| canary/team/18, canary/team/7 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "e7f0c589821b", "n1": "ce420cbf9bea", "n2": "a3f6a6cf2241", "r": "125bc0a228c2"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| canary/team-fullrecord/18, canary/team-fullrecord/7 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "e7f0c589821b", "n1": "ce420cbf9bea", "n2": "f25f4a28508f", "r": "125bc0a228c2"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| canary/hint/18, canary/hint/7 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "34931229381b", "n1": "20b3e5760122", "n2": "8a61c8da3e82", "r": "92b6e15fdae6"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| canary/team/10, canary/team/15, canary/team/17, canary/team/22, canary/team/8 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "6ebca790d7f4", "n1": "ce420cbf9bea", "n2": "a3f6a6cf2241", "r": "125bc0a228c2"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| canary/team-fullrecord/10, canary/team-fullrecord/15, canary/team-fullrecord/17, canary/team-fullrecord/22, canary/team-fullrecord/8 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "6ebca790d7f4", "n1": "ce420cbf9bea", "n2": "f25f4a28508f", "r": "125bc0a228c2"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| canary/hint/10, canary/hint/15, canary/hint/17, canary/hint/22, canary/hint/8 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "dd12751f3112", "n1": "20b3e5760122", "n2": "8a61c8da3e82", "r": "92b6e15fdae6"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/solo/0 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "d2249431bc41"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team/0 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "2c13c0be5263", "n1": "bf8468c93678", "n2": "9de10b09909a", "r": "9515962bf122", "s": "925d7d321902"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team-fullrecord/0 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "2c13c0be5263", "n1": "bf8468c93678", "n2": "6bc0901ca085", "r": "9515962bf122", "s": "925d7d321902"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/hint/0 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "52d78d440a63", "n1": "e4db38bb4ef8", "n2": "991166a38248", "r": "3a7af5a94155", "s": "e5c846ef1f3c"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/solo/1 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "3f7c8c25c767"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team/1 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "7f11a608a987", "n1": "86077a0c0e6c", "n2": "69e26d8ff0f3", "r": "9515962bf122", "s": "7fad765151cf"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team-fullrecord/1 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "7f11a608a987", "n1": "86077a0c0e6c", "n2": "e6b6b34769c4", "r": "9515962bf122", "s": "7fad765151cf"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/hint/1 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "023c81f1f3de", "n1": "ebe0650ef087", "n2": "4361363c211e", "r": "3a7af5a94155", "s": "5efd352a12f7"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/solo/2 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "73fb0c80ab3c"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team/2 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "c4dce5806c61", "n1": "bf8468c93678", "n2": "73066573e484", "r": "9515962bf122", "s": "aa9915486a4e"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team-fullrecord/2 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "c4dce5806c61", "n1": "bf8468c93678", "n2": "bffb6615a88a", "r": "9515962bf122", "s": "aa9915486a4e"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/hint/2 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "bf973ab5b55b", "n1": "e4db38bb4ef8", "n2": "7136b3f3c03f", "r": "3a7af5a94155", "s": "cb7f1fa4341a"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/solo/3 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "2eb7f6340c5a"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team/3 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "9a6f767725d5", "n1": "c2f46d118b67", "n2": "69e26d8ff0f3", "r": "9515962bf122", "s": "60d90c5d57dd"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team-fullrecord/3 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "9a6f767725d5", "n1": "c2f46d118b67", "n2": "e6b6b34769c4", "r": "9515962bf122", "s": "60d90c5d57dd"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/hint/3 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "ac4b58e815b5", "n1": "bd8cf87488f2", "n2": "4361363c211e", "r": "3a7af5a94155", "s": "054dfdfbab1f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/solo/4 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "aa51e01fed80"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team/4 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "0b042fd76ef3", "n1": "c43d7e84a39b", "n2": "6d93d4a28ae4", "r": "9515962bf122", "s": "82afe25eb3cf"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team-fullrecord/4 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "0b042fd76ef3", "n1": "c43d7e84a39b", "n2": "e70b3dfdc097", "r": "9515962bf122", "s": "82afe25eb3cf"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/hint/4 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "c1186ff234c6", "n1": "7634318c4d73", "n2": "b5f55d892236", "r": "3a7af5a94155", "s": "1e1ace5baaaf"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/solo/5 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "d304f7a13bea"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team/5 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "029504e935dd", "n1": "bf8468c93678", "n2": "69e26d8ff0f3", "r": "9515962bf122", "s": "deb7ad1d5199"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team-fullrecord/5 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "029504e935dd", "n1": "bf8468c93678", "n2": "e6b6b34769c4", "r": "9515962bf122", "s": "deb7ad1d5199"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/hint/5 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "db39f5f21d8a", "n1": "e4db38bb4ef8", "n2": "4361363c211e", "r": "3a7af5a94155", "s": "487505ff7523"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/solo/6 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "aadd14f9cab1"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team/6 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "002da415a568", "n1": "bf8468c93678", "n2": "4810b1897254", "r": "9515962bf122", "s": "61084962b231"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team-fullrecord/6 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "002da415a568", "n1": "bf8468c93678", "n2": "7c56ece1cadd", "r": "9515962bf122", "s": "61084962b231"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/hint/6 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "e124520dbeff", "n1": "e4db38bb4ef8", "n2": "08f05c527056", "r": "3a7af5a94155", "s": "31aef84ff694"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/solo/7 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "259c278644fe"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team/7 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "a30c34f537b2", "n1": "bf8468c93678", "n2": "69e26d8ff0f3", "r": "9515962bf122", "s": "045922e0227b"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team-fullrecord/7 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "a30c34f537b2", "n1": "bf8468c93678", "n2": "e6b6b34769c4", "r": "9515962bf122", "s": "045922e0227b"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/hint/7 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "8a0917f14bf0", "n1": "e4db38bb4ef8", "n2": "4361363c211e", "r": "3a7af5a94155", "s": "f68329811187"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/solo/8 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "8886bc510b97"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team/8 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "e14ac8e18942", "n1": "bf8468c93678", "n2": "6d93d4a28ae4", "r": "9515962bf122", "s": "aa9915486a4e"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team-fullrecord/8 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "e14ac8e18942", "n1": "bf8468c93678", "n2": "e70b3dfdc097", "r": "9515962bf122", "s": "aa9915486a4e"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/hint/8 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "f024f243f392", "n1": "e4db38bb4ef8", "n2": "b5f55d892236", "r": "3a7af5a94155", "s": "cb7f1fa4341a"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/solo/9 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "d5af513ff3a0"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team/9 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "7760ff858dc4", "n1": "bf8468c93678", "n2": "f65a65719370", "r": "9515962bf122", "s": "2f3738375cc2"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team-fullrecord/9 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "7760ff858dc4", "n1": "bf8468c93678", "n2": "07712de881e3", "r": "9515962bf122", "s": "2f3738375cc2"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/hint/9 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "b1bf79b37456", "n1": "e4db38bb4ef8", "n2": "7cb759ac8795", "r": "3a7af5a94155", "s": "bac91010d47c"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/solo/10 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "88e8239868ac"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team/10 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "d751a30b12a1", "n1": "bf8468c93678", "n2": "30615e4777d9", "r": "9515962bf122", "s": "4294f327cf41"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team-fullrecord/10 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "d751a30b12a1", "n1": "bf8468c93678", "n2": "eed5c7a6cbc9", "r": "9515962bf122", "s": "4294f327cf41"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/hint/10 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "02d82fa07571", "n1": "e4db38bb4ef8", "n2": "71c4c15d6f1f", "r": "3a7af5a94155", "s": "bd456e818f12"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/solo/11, standard/solo/17 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "f2feef7a2711"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team/11 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "e3f334424901", "n1": "bf8468c93678", "n2": "69e26d8ff0f3", "r": "9515962bf122", "s": "9aa78f1e6940"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team-fullrecord/11 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "e3f334424901", "n1": "bf8468c93678", "n2": "e6b6b34769c4", "r": "9515962bf122", "s": "9aa78f1e6940"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/hint/11 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "a3edfbfe6b97", "n1": "e4db38bb4ef8", "n2": "4361363c211e", "r": "3a7af5a94155", "s": "c88eb5fd580b"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/solo/12 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "fa1c181e81e1"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team/12 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "5c8f4483bfc6", "n1": "bf8468c93678", "n2": "118453311c99", "r": "9515962bf122", "s": "b22871e7253c"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team-fullrecord/12 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "5c8f4483bfc6", "n1": "bf8468c93678", "n2": "8ff8d0b4ecd9", "r": "9515962bf122", "s": "b22871e7253c"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/hint/12 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "70a2b58439e0", "n1": "e4db38bb4ef8", "n2": "ce2d9d5627e4", "r": "3a7af5a94155", "s": "7776253fdcec"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/solo/13 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "71a732569812"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team/13 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "15186cbc508e", "n1": "bf8468c93678", "n2": "69e26d8ff0f3", "r": "9515962bf122", "s": "6e06855bd87e"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team-fullrecord/13 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "15186cbc508e", "n1": "bf8468c93678", "n2": "e6b6b34769c4", "r": "9515962bf122", "s": "6e06855bd87e"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/hint/13 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "2a84c982e712", "n1": "e4db38bb4ef8", "n2": "4361363c211e", "r": "3a7af5a94155", "s": "4d7c8ff247d4"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/solo/14 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "1a8559dc98c0"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team/14 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "958c0f957f3a", "n1": "ac690aa4136c", "n2": "30615e4777d9", "r": "9515962bf122", "s": "b55886f5196c"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team-fullrecord/14 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "958c0f957f3a", "n1": "ac690aa4136c", "n2": "eed5c7a6cbc9", "r": "9515962bf122", "s": "b55886f5196c"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/hint/14 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "685cdbd4cf56", "n1": "6e44dcece472", "n2": "71c4c15d6f1f", "r": "3a7af5a94155", "s": "7abb6dc0b269"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/solo/15 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "bc9a632c2457"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team/15 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "25633959463a", "n1": "86077a0c0e6c", "n2": "69e26d8ff0f3", "r": "9515962bf122", "s": "ba9e53326c47"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team-fullrecord/15 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "25633959463a", "n1": "86077a0c0e6c", "n2": "e6b6b34769c4", "r": "9515962bf122", "s": "ba9e53326c47"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/hint/15 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "14d565c0872d", "n1": "ebe0650ef087", "n2": "4361363c211e", "r": "3a7af5a94155", "s": "22635f351632"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/solo/16 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "df75bda5292e"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team/16 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "a31e93a27a94", "n1": "c2f46d118b67", "n2": "cef19bbf35ee", "r": "9515962bf122", "s": "03529d6cd1c1"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team-fullrecord/16 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "a31e93a27a94", "n1": "c2f46d118b67", "n2": "a49f99fd4380", "r": "9515962bf122", "s": "03529d6cd1c1"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/hint/16 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "a6a53a54bdb2", "n1": "bd8cf87488f2", "n2": "b2e221f2ea83", "r": "3a7af5a94155", "s": "8562db1f8124"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team/17 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "73d99e7fc3dc", "n1": "bf8468c93678", "n2": "30615e4777d9", "r": "9515962bf122", "s": "074009df5288"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team-fullrecord/17 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "73d99e7fc3dc", "n1": "bf8468c93678", "n2": "eed5c7a6cbc9", "r": "9515962bf122", "s": "074009df5288"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/hint/17 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "edf659a1055b", "n1": "e4db38bb4ef8", "n2": "71c4c15d6f1f", "r": "3a7af5a94155", "s": "482d6b843973"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/solo/18 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "594059d889c8"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team/18 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "629aaa47ff8e", "n1": "eeee9efece66", "n2": "30615e4777d9", "r": "9515962bf122", "s": "7196a0845ba7"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team-fullrecord/18 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "629aaa47ff8e", "n1": "eeee9efece66", "n2": "eed5c7a6cbc9", "r": "9515962bf122", "s": "7196a0845ba7"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/hint/18 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "9b529b866805", "n1": "b24223defcc9", "n2": "71c4c15d6f1f", "r": "3a7af5a94155", "s": "9c80bec5b9bc"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/solo/19 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "0fd2c15bb02c"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team/19 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "5a12cb9c199d", "n1": "0bb4b5012f9e", "n2": "94ecc83569c1", "r": "9515962bf122", "s": "cb63a070c40a"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team-fullrecord/19 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "5a12cb9c199d", "n1": "0bb4b5012f9e", "n2": "2f8d23c4517c", "r": "9515962bf122", "s": "cb63a070c40a"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/hint/19 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "cc2f4e7b25f8", "n1": "eb399415fc04", "n2": "88346588930b", "r": "3a7af5a94155", "s": "cb45e25cb662"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/solo/20 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "f6f435520671"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team/20 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "ff911a5d6457", "n1": "0bb4b5012f9e", "n2": "69e26d8ff0f3", "r": "9515962bf122", "s": "3c9ca2b1cf9f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team-fullrecord/20 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "ff911a5d6457", "n1": "0bb4b5012f9e", "n2": "e6b6b34769c4", "r": "9515962bf122", "s": "3c9ca2b1cf9f"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/hint/20 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "b208f1c69599", "n1": "eb399415fc04", "n2": "4361363c211e", "r": "3a7af5a94155", "s": "9784dac578d9"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/solo/21 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "ee185f1ce29c"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team/21 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "4cf58db8947e", "n1": "b141f3dcfbe7", "n2": "69e26d8ff0f3", "r": "9515962bf122", "s": "cc8696ab6313"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team-fullrecord/21 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "4cf58db8947e", "n1": "b141f3dcfbe7", "n2": "e6b6b34769c4", "r": "9515962bf122", "s": "cc8696ab6313"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/hint/21 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "80e22fb0b8df", "n1": "68ecf738734c", "n2": "4361363c211e", "r": "3a7af5a94155", "s": "047a11c7a71d"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/solo/22 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "37e5e0ec07a8"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team/22 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "8d410d15c1e2", "n1": "bf8468c93678", "n2": "cef19bbf35ee", "r": "9515962bf122", "s": "4cfc29b2290c"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team-fullrecord/22 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "8d410d15c1e2", "n1": "bf8468c93678", "n2": "a49f99fd4380", "r": "9515962bf122", "s": "4cfc29b2290c"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/hint/22 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "61874997995c", "n1": "e4db38bb4ef8", "n2": "b2e221f2ea83", "r": "3a7af5a94155", "s": "347f402875c7"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/solo/23 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "b0a0bf5fd689"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team/23 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "118f13c8d0d9", "n1": "bf8468c93678", "n2": "f65a65719370", "r": "9515962bf122", "s": "1cf1cf030769"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/team-fullrecord/23 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "118f13c8d0d9", "n1": "bf8468c93678", "n2": "07712de881e3", "r": "9515962bf122", "s": "1cf1cf030769"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |
| standard/hint/23 | `{"extension": "b04f3b913d6a", "family": "962067543348", "family_version": 2, "instructions": {"a": "7d144ba067ec", "n1": "e4db38bb4ef8", "n2": "7cb759ac8795", "r": "3a7af5a94155", "s": "994feb7bfebc"}, "policies": "28c7ea5afd3f", "policy_runner": "5722b6365ad1", "world": "7e49d8785fca"}` |

## Load and cost (estimates)

Estimate: 4 characters per input token; repeat the instruction, JSON briefing and seat files once per simulated world request (at least once per model seat). Excludes scripted seats, system prompts, replies, history and output tokens. Requests are not measured model turns.

| Level / arm | Per-seat max calls / briefing chars | Mean estimated tokens | $1 / $5 / $15 per M |
|---|---|---|---|
| canary / solo | a: 29/136 | 36534 | $0.03653 / $0.18267 / $0.54800 |
| canary / team | a: 15/119, n1: 9/111, n2: 16/119, r: 3/104 | 40714 | $0.04071 / $0.20357 / $0.61072 |
| canary / team-fullrecord | a: 15/119, n1: 9/111, n2: 16/119, r: 3/104 | 40779 | $0.04078 / $0.20389 / $0.61168 |
| canary / hint | a: 15/119, n1: 9/111, n2: 16/119, r: 3/104 | 42008 | $0.04201 / $0.21004 / $0.63012 |
| standard / solo | a: 125/268 | 164333 | $0.16433 / $0.82167 / $2.46500 |
| standard / team | a: 59/173, n1: 51/171, n2: 43/162, r: 3/108, s: 78/206 | 232153 | $0.23215 / $1.16077 / $3.48230 |
| standard / team-fullrecord | a: 59/173, n1: 51/171, n2: 43/162, r: 3/108, s: 78/206 | 232356 | $0.23236 / $1.16178 / $3.48535 |
| standard / hint | a: 59/173, n1: 51/171, n2: 43/162, r: 3/108, s: 78/206 | 238732 | $0.23873 / $1.19366 / $3.58097 |

## Evidence and predictions

[flotilla/docs/seams/week.md](file:///Users/petermakhnatch/Developer/flotilla-wt/seams/flotilla/docs/seams/week.md)

## Open risks

- NA: separation
- NA: dose response
- NA: one seat is not enough

Scripted policy results are not estimates of frontier-model performance.
