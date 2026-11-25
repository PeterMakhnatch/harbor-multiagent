# Seams: what is lost at a boundary between agent contexts

Seams is a suite of multi-agent evals built with the Flotilla kit, and the tools to build more of them. A
multi-agent system is several separate contexts: each agent sees its own part of the work. Seams measures what
is lost at the boundary between two contexts: what one agent says, stores or compresses for another, and what
two agents must keep in sync.

Each family tests one proposed failure between contexts. A knowledgeable seat with the full record should
succeed. A low score alone does not establish the cause; use matched controls and the world events.

**Status (2026-11-25).** No paid model run exists. The numbers come from 3 sources, always labelled: scripted
policies in the simulator; the `oracle` agent on local Docker ($0); and live playtests, where a real
command-line assistant sits in every model seat through its subscription (`flotilla/playtest.py`). A playtest
is not an experiment: n is small and the harness is the assistant's own CLI.

### Try one boundary without a model

From the fork root, run this with Python 3.12 and PyYAML installed. No Docker or API key is needed.

```sh
python3 flotilla/sim.py --family handoff --difficulty standard \
  --arms team,team-fullrecord --policies careful,pointer --instances 0 --schedule-seeds 0
```

The careful policy wins in both arms. The pointer policy scores 0.5 with the note alone and 1 with the full
record. It does the same work in both cases. What changes is whether the next shift can look up the batch
named in the note. This is a scripted example, not a result about models.

## What the live playtests showed

Two frontier assistants (GPT-6.2 Sol in the Codex CLI, Claude Opus 5.5 in Claude Code) played every family.
Two smaller models (GPT-6 Luna, Claude Haiku 5.5) played a part of them.

| Family | Seam | Frontier result (wins / playtests) | Smaller models | Status |
|---|---|---|---|---|
| `handoff` | One shift writes a note for the next shift | Repaired build, facts left unstated (0 / 1 / 3): Opus 4/5, 8/11, 2/5. Sol 4/4 with 1 fact unstated | 0/6 on the earlier build | Repeated loss of evidence in notes; no reliable effect size for the wording change |
| `digest` | A chain of capped notes with replacement rules | 52 of 52 (base levels, order levels, and the twin `team-implicit` 16 of 16) | 0 of 8; 73 of 90 wrong jobs at the seam | Canary for frontier models in every arm; a measure for smaller models |
| `commit` | A notice that arrives before an irreversible booking | 51 of 52 (base and bulk levels) | 5 of 6 | Canary |
| `tell` | A fact that matters only to another desk | 28 of 28 | Luna 1 of 2, Haiku 1 of 1 | Canary for frontier models |
| `interface` | A setting that only another desk knows | 8 of 8 live teams; 28 of 28 pair runs, 0 premature orders | Luna 0 of 1, Haiku 1 of 1 | Canary. The pair `probe-wait` does not yet measure waiting (see the family page) |
| `settle` | Agree on one value with delayed messages | 45 of 46 (the loss was a scripted-desk defect); the pair `team-ownorder` / `team-implicit` 26 of 26 | not played | Canary in every arm |
| `week` | 4 seams in one scenario of 4 or 5 desks | 8 of 12 attempts finished at the reference value or within EUR 2; 1 flagged zero and 3 early attempts with no trial result | not played | Integration scenario, not a measure |

The first reading, with the limits of a playtest:

1. The other families mostly serve as controls for these frontier models. A capped note or an unusual work
   order did not by itself make the teams fail. A single failure in `commit` came from a seat hiding a reply
   in its own script.
2. In `handoff`, the writer sometimes leaves a batch ID, not the lot IDs the next shift needs. On the
   repaired build, all 7 Opus losses began that way. The reference pairs and settlements were correct; the
   last shift could not identify the lot. A sufficient first note uses 97 of the 200 characters.
   This happened even with the desk rule stated. On the 5 instances played in both arms, Opus won 4/5 with
   the rule and 5/5 without it. The original control had named the lot list and given a misleading desk
   view. Do not pool the builds or treat the original wording contrast as a measured boundary cost.
3. In `week`, the first tool version wrote no result for 3 stalled teams. A later version recorded an
   unfinished week as a valid zero. Report these separately. The world now has a wait call that does not
   require the seat to compute a tick (`/await?until=work`).
4. The 2 smaller models won 8 of 25 playtests. They won none of the 8 `digest` or 6 `handoff` playtests.

## Two kinds of family

| Kind | Families | What they measure |
|---|---|---|
| Boundary | `handoff`, `digest` | The cost of passing work through a capped note to a fresh context. The reader can be the same model or a teammate. |
| Two-sided | `interface`, `commit`, `tell`, `settle` | A live other agent makes its own choices at the same time; the seats must look, ask, tell or agree before an irreversible step. |

The scenario `week` composes 4 seams in one dispatch week of a fictional company. It is an integration test
and a demonstration, not a headline number.

Each family reports costs as paired differences of arms on the same instances: the boundary cost (the control
without the boundary minus `team`), the teammate cost (`solo-seam` minus `team`), the protocol cost (`hint`
minus `team`) and, where offered, the stated-fact contrast (`team` minus `team-implicit`).

## The standard

1. Easy alone: `solo` (1 seat, all the information) is solved by a careful agent at every dose level.
2. One seam: the team condition differs from the control by one stated mechanism.
3. Attribution: probe arms (1 seat under test, scripted teammates, a trap against a matched control that differs
   in one variable) measure the behaviour from world events. No LLM judge.
4. No tricks: every rule that matters is stated to the seat that needs it, or it is in that seat's own view; the
   world never lies.
5. Deterministic scoring from world events; logical time; the grader accepts every valid answer; the chance
   baseline is computed.
6. One seat alone cannot score; no text that a seat reads shows the answer or the arm.
7. The seat truly lacks what crosses the seam.
8. Messages are pushed with the reply to the seat's next call; receipts record what entered each seat's view.
9. A seat's own omission is a valid flagged result and counts as a failure. Only an infrastructure fault is
   invalid.

## Conditions and dose levels

| Arm | Meaning |
|---|---|
| `solo` | 1 seat, all the information |
| `team-fullrecord` | Boundary families: a fresh seat per shift or hop that can read the full record; the primary control |
| `solo-seam` | 1 model passes the same seam with no teammate |
| `team` | Every seat is a model |
| `team-implicit` | `team` without one stated fact about the other seat's view |
| `probe-…-trap`, `probe-…-control` | 1 seat under test, scripted teammates; the pair differs in one variable |
| `hint` | `team` plus the one sentence that names the protocol |

Dose levels are `canary`, `standard` and `heavy`; a family can declare more (`standard-bulk`, `standard-order`).
The rules are the same at every level. The load sits on the seam: per-seat turns stay about constant.

## Tools

| Tool | Use |
|---|---|
| `flotilla/sim.py` | Runs a task in process with one scripted policy per seat and a seeded schedule; about 130 simulations per second |
| Policies (`flotilla/policies/<family>.py`) | `careful`, `naive`, `nop`, `lapse-P`, blind guess, `lone`, and the slips seen in live play (`pointer`, `script-filter`, `renumber`); the same code runs in the simulator and in a seat container |
| `flotilla/eval_card.py` | Prints a family's validity evidence: the policy matrix, 17 checks, the costs, the power plan, load and cost; `--suite` makes one table for all families |
| `flotilla/playtest.py` | Runs a built task on the host with a real assistant in each seat under a sandbox profile; `--check-seat` tests the seat's shell with no model |
| `flotilla/token_profile.py`, `flotilla/pilot_plan.py` | A token profile from playtest logs or from a turn model; a gated pilot plan with a price forecast. Neither starts a run |
| `flotilla/run_wave.py` | Pre-registered waves with a cost forecast, caps, and stages with gates (`--stop-after-stage`) |
| `flotilla/oracle_parity.py` | Compares a frozen local Docker oracle rehearsal to the simulator on the same built tasks. A missing, invalid or extra result is not a match |
| `flotilla/handoff_receipts.py` | Tests a memory aid offline. For each batch ID in a handoff note, recover the lots from a response delivered to its writer before filing. Report missing evidence and byte cost. Never change the score |
| `start_after` (Harbor core) | A seat's agent starts only when the listed seats' agents have ended: chains of shifts |

A family is 3 files that the kit discovers: `flotilla/seams/<family>.py` (generator, arms, instruction text,
metadata), `flotilla/world/ext/<family>.py` (the rules and the score) and `flotilla/policies/<family>.py`. See
CONTRACT.md section 22, AUTHORING.md (how to write a family, 27 rules) and PILOT.md (the protocol draft for a
first paid run).

## What a number needs before anyone should trust it

- A paired design with about 100 instances per cell for a 15-point effect (the card's power plan gives the n).
- Two harnesses (Terminus 2 and one native command-line agent); the card names the harness and the delivery mode.
- The canary string of the suite in every built task, so that training-data contamination can be detected.
- A human baseline before publication.

## Kill rules

A family is retired for a model class when: a frontier `team` scores 0.9 or more of its control at `standard`
and `hint` closes the rest; the effect reverses between 2 harnesses; or `solo` falls below 0.9 at `heavy`.

## Sources

Research notes, the first designs, the final specifications, the independent reviews, every playtest report
and the amendments are in Peter's library: `research-context/bridge/2026-11-19-flotilla/seams/`. The cited
papers were checked in part by a reviewer; the family pages label each citation OBSERVED or CLAIMED.
