# Seams: what is lost at a boundary between agent contexts

Seams is a suite of multi-agent evals built with the Flotilla kit, and the tools to build more of them. A
multi-agent system is several separate contexts: each agent sees its own part of the work. Seams measures what
is lost at the boundary between two contexts: what one agent says, stores or compresses for another, and what
two agents must keep in sync.

Every task is easy for 1 agent that holds all the information. The same work is hard across a boundary for
exactly one named reason. A low score then means a boundary problem, not a hard task and not a trick.

**Status (2026-11-25).** No paid model run exists. The numbers come from 3 sources, always labelled: scripted
policies in the simulator; the `oracle` agent on local Docker ($0); and live playtests, where a real
command-line assistant sits in every model seat through its subscription (`flotilla/playtest.py`). A playtest
is not an experiment: n is small and the harness is the assistant's own CLI.

## What the live playtests showed

Two frontier assistants (GPT-6.2 Sol in the Codex CLI, Claude Opus 5.5 in Claude Code) played every family.
Two smaller models (GPT-6 Luna, Claude Haiku 5.5) played a part of them.

| Family | Seam | Frontier result (wins / playtests) | Smaller models | Status |
|---|---|---|---|---|
| `handoff` | A capped shift note to a fresh context | At `standard`: `team` (fact stated) 12 of 12, `hint` 8 of 8, `team-fullrecord` 5 of 5; **`team-implicit`: Opus 6 of 14, Sol 11 of 11** | 0 of 6; 5 of 6 losses at the seam | Candidate measure for a frontier pilot (`team` / `team-implicit` / `hint` / `team-fullrecord`) |
| `digest` | A chain of capped notes with replacement rules | 34 of 34 (base and order levels) | 0 of 8; 73 of 90 wrong jobs at the seam | Canary for frontier models; a measure for smaller models. Twin `team-implicit`: see the family page |
| `commit` | A notice that arrives before an irreversible booking | 51 of 52 (base and bulk levels) | 5 of 6 | Canary |
| `tell` | A fact that matters only to another desk | 28 of 28 | Luna 1 of 2, Haiku 1 of 1 | Canary for frontier models |
| `interface` | A setting that only another desk knows | 8 of 8 live teams; 28 of 28 pair runs, 0 premature orders | Luna 0 of 1, Haiku 1 of 1 | Canary. The pair `probe-wait` does not yet measure waiting (see the family page) |
| `settle` | Agree on one value with delayed messages | 19 of 20 (the loss was a scripted-desk defect) | not played | Canary with a shared sorted list; pair `team-ownorder` / `team-implicit`: see the family page |
| `week` | All seams in one scenario of 4 or 5 desks | 8 of 12 teams at the reference value or EUR 2 below it; no seam failure in a finished run; 4 teams ended by 1 seat (1 refusal, 3 wait-call errors) | not played | Integration scenario, not a measure |

The first reading, with the limits of a playtest:

1. Frontier assistants carry what they are told to carry. A capped note, a notice in a reply, an unasked
   forward and an exact key form cost them nothing at these sizes. The reviewers had predicted this for every
   family, and the suite's own kill rule now marks those arms as canaries for this model class.
2. The loss that did appear is an inference about the reader. In `handoff`, Opus wrote "the bad lot is in
   B95" in its note. B95 then left the desk, and the next shift could not see its lots. With one stated fact
   ("Once a batch is settled, it leaves the desk; no later shift can see its lot list on the desk") Opus won 6
   of 6 playtests on the instances where it had lost 8 of 9. Sol listed the lots in both arms. The twin arm
   `team-implicit` keeps this loss measurable; AUTHORING.md rule 25 makes it a pattern for the other families.
3. A team is as weak as its most confused seat. 4 of 12 frontier `week` teams were ended by 1 seat that
   refused the work or misused a wait call; no team of the other families was. These are valid flagged
   results, and the wait call now has a form with no number to get wrong (`/await?until=work`).
4. The families separate model classes. On the same tasks the 2 smaller models lost in 15 of 23 playtests, and
   most losses are at the seam, not in the seat's own work.

## Two kinds of family

| Kind | Families | What they measure |
|---|---|---|
| Boundary | `handoff`, `digest` | The cost of passing work through a capped note to a fresh context. The reader can be the same model or a teammate. |
| Two-sided | `interface`, `commit`, `tell`, `settle` | A live other agent makes its own choices at the same time; the seats must look, ask, tell or agree before an irreversible step. |

The scenario `week` composes 4 seams in one dispatch week of a fictional company. It is an integration test
and a demonstration, not a headline number.

Each family reports costs as paired differences of arms on the same instances: the boundary cost (the control
without the boundary minus `team`), the teammate cost (`solo-seam` minus `team`), the protocol cost (`hint`
minus `team`) and, where the family has the twin, the inference cost (`team` minus `team-implicit`).

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
| `start_after` (Harbor core) | A seat's agent starts only when the listed seats' agents have ended: chains of shifts |

A family is 3 files that the kit discovers: `flotilla/seams/<family>.py` (generator, arms, instruction text,
metadata), `flotilla/world/ext/<family>.py` (the rules and the score) and `flotilla/policies/<family>.py`. See
CONTRACT.md section 22, AUTHORING.md (how to write a family, 25 rules) and PILOT.md (the protocol draft for a
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
