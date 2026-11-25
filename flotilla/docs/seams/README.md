# Seams: what is lost at a boundary between agent contexts

Seams is a suite of multi-agent evals built with the Flotilla kit. A multi-agent system is several separate
contexts: each agent sees its own part of the work. Seams measures what is lost at the boundary between two
contexts: what one agent says, stores or compresses for another, and what two agents must keep in sync.

Every task is easy for 1 agent that holds all the information. The same work is hard across a boundary for
exactly one named reason. A low score then means a boundary problem, not a hard task and not a trick.

Nothing in this folder is a model result. Every number is from scripted policies in the simulator or from the
`oracle` agent on local Docker, at $0.

## Two kinds of family

| Kind | Families | What they measure |
|---|---|---|
| Boundary | `handoff`, `digest` | The cost of passing work through a capped note to a fresh context. The reader can be the same model or a teammate. |
| Two-sided | `interface`, `commit` | A live other agent makes its own choices at the same time; the seats must look, ask or read before an irreversible step. |

The scenario `week` composes the 4 seams in one dispatch week of a fictional company. It is an integration test
and a demonstration, not a headline number.

Each family reports 3 costs as paired differences of arms: the boundary cost (the control without the boundary
minus `team`), the teammate cost (`solo-seam` minus `team`) and the protocol cost (`hint` minus `team`).

## The standard

1. Easy alone: `solo` (1 seat, all the information) is solved by a careful agent at every dose level.
2. One seam: the team condition differs from the control by one stated mechanism.
3. Attribution: probe arms (1 seat under test, scripted teammates, a trap against a matched control that differs
   in one variable) measure the behaviour from world events. No LLM judge.
4. No tricks: every rule that matters is stated to the seat that needs it; the world never lies.
5. Deterministic scoring from world events; logical time; the grader accepts every valid answer; the chance
   baseline is computed.
6. One seat alone cannot score; no text that a seat reads shows the answer or the arm.
7. The seat truly lacks what crosses the seam.
8. Messages are pushed with the reply to the seat's next call; receipts record what entered each seat's view.

## Conditions and dose levels

| Arm | Meaning |
|---|---|
| `solo` | 1 seat, all the information |
| `team-fullrecord` | Boundary families: a fresh seat per shift or hop that can read the full record; the primary control |
| `solo-seam` | 1 model passes the same seam with no teammate |
| `team` | Every seat is a model |
| `probe-…-trap`, `probe-…-control` | 1 seat under test, scripted teammates; the pair differs in one variable |
| `hint` | `team` plus the one sentence that names the protocol |

Dose levels are `canary`, `standard` and `heavy`. The rules are the same at every level. The load sits on the
seam: per-seat turns stay about constant, and the knob is crossings, amendments, visibility or the distance
between a delivery and the next irreversible step.

## Tools

| Tool | Use |
|---|---|
| `flotilla/sim.py` | Runs a task in process with one scripted policy per seat and a seeded schedule; about 130 simulations per second |
| Policies (`flotilla/policies/<family>.py`) | `careful`, `naive`, `nop`, `lapse-P` (careful with a seeded lapse rate), blind guess, `lone`; the same code runs in the simulator and in a seat container |
| `flotilla/eval_card.py` | Prints a family's validity evidence: the policy matrix, 13 checks, the 3 costs, the power plan, load and cost |
| `flotilla/playtest.py` | Runs a built task locally with an external command in a seat, to watch a real assistant before any paid run |
| `start_after` (Harbor core) | A seat's agent starts only when the listed seats' agents have ended: chains of shifts |
| `flotilla/run_wave.py` | Pre-registered waves with a cost forecast, caps and staged early stop |

A family is 3 files that the kit discovers: `flotilla/seams/<family>.py` (generator, arms, instruction text,
metadata), `flotilla/world/ext/<family>.py` (the rules and the score) and `flotilla/policies/<family>.py`. See
CONTRACT.md section 22 and README "Add a family".

## What a number needs before anyone should trust it

- A paired design with about 100 instances per cell for a 15-point effect (the card's power plan gives the n).
- Two harnesses (Terminus 2 and one native command-line agent); the card names the harness and the delivery mode.
- The canary string of the suite in every built task, so that training-data contamination can be detected.
- A human baseline before publication.

## Kill rules

A family is retired for a model class when: a frontier `team` scores 0.9 or more of its control at `standard`
and `hint` closes the rest; the effect reverses between 2 harnesses; or `solo` falls below 0.9 at `heavy`.

## Sources

Research notes, the first designs, the final specifications, the independent skeptic review and the amendments
are in Peter's library: `research-context/bridge/2026-11-19-flotilla/seams/`. The cited papers were checked in
part by a reviewer; the family pages label each citation OBSERVED or CLAIMED.
