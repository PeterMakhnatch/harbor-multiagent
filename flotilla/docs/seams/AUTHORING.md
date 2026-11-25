# Seams: how to build a family that survives review

This page is for the author of a new family. It lists the rules that the first families broke, and the steps
that a family passes before any paid run. Every rule came from a finding with evidence in an independent review
of a first build. The contract for the 3 files of a family is CONTRACT.md section 22.

## The ladder: what a family passes, in order

| Step | Tool | Cost | A family passes when |
|---|---|---|---|
| 1. Simulation sweeps | `flotilla/sim.py`, the family's tests | $0 | `careful` scores 1 in every arm and level; `naive` scores 1 in controls and loses in traps; `nop` is at the floor; replay is exact |
| 2. Eval card | `flotilla/eval_card.py --family <f>` | $0 | No check fails; every NA has a reason; the power plan gives the n |
| 3. Built tasks | `build_tasks.py`, `qa.py` | $0 | 0 FAIL: no answer, arm or canary string in any text that a seat reads |
| 4. Oracle parity | Harbor `oracle` agent on local Docker | $0 | The rewards of `careful` and `naive` builds equal the simulator's |
| 5. Independent review | 2 reviewers, see below | $0 | No open BLOCKER or MAJOR finding |
| 6. Live playtest | `flotilla/playtest.py` with a command-line assistant in a seat | subscription only | A real assistant can do the work from the briefing; no sentence misleads it |
| 7. Pilot | `flotilla/run_wave.py`, staged, pre-registered | paid, capped | See PILOT.md |

A family that fails a step goes back to its author. Step 5 found 11 distinct BLOCKER findings in the first 5
builds, with no model call.

## Rules for scripted teammates

1. A scripted teammate never decides a model seat's score by parsing free text with one pattern. State the exact
   format in the common briefing of every arm, or accept every reasonable form and record an unreadable text as
   its own outcome (`unparsed`), never as a silent 0.
2. A scripted teammate does only what a live teammate could do under the same rules. The seat cannot tell a probe
   arm from `team` by a reply field, a timing or a status flag.
3. A scripted teammate does not give the hidden fact for free. It answers a direct question that names the thing.
   A greeting gets no fact. The measure `asked` counts questions, not messages.
4. In a probe pair, the same party does the scored action in the trap and in the control. The pair differs in one
   variable in what the seat reads and in what the world does.

## Rules for the score and for chance

5. Find the informed-guess ceiling with alternative scripted players before you state chance: a fixed default,
   the most common action, a guess from visible structure (sizes, counts, order, round numbers), a default that 2
   seats of the same model would both choose. Remove every visible feature that predicts the answer. Report the
   ceiling as the chance baseline. Print values in a neutral form that suggests no default.
6. A guess does not pay. A wrong irreversible action costs more than no action, or credit needs the evidence
   event (the result was received, the notice was in view) before the action.
7. Empty, partial and repeated submissions are refused with a clear error or handled by a stated rule.
8. `canary` has few items, so chance is high there. `canary` is a fairness and plumbing check. Make no
   measurement claim at `canary`.

## Rules for outcomes

9. `score.json` keeps 3 outcomes apart: a valid reward; a seat's own omission (it stopped, never handed over,
   never stepped), which is a valid result with a named flag; and an infrastructure fault, which is invalid. A
   stalling seat does not turn a team's failure into an invalid trial that the analysis drops. The kit hooks are
   `on_seat_end`, `pending` and `on_deadline`.
10. The outcome of `team` never depends on which seat's call reaches the world first in wall-clock time. Use the
    world's logical time and a round barrier.

## Rules for the briefing

11. The base briefing states facts about the world: what a reply can contain, what is final, when a rule applies.
    It does not state the habit that the family measures. That sentence is the `hint` arm. If the fact alone
    makes the habit obvious to any attentive agent, say so in the family page.
12. If a seat can get the fact by a second route, that route is valid and scored as correct. The page claims only
    what the events prove: a reply written to the socket is "delivered", not "read".
13. Every sentence is true in every arm and at every level. When a mechanism changes, read again every sentence
    that names a time.
14. State the call budget, what counts toward it, and reserve the calls that the seam needs. `/await`, `/whoami`
    and `/health` are free. Set the play and agent time limits per level (`play_timeout_sec`,
    `agent_timeout_sec`).

## Rules for the dose and the measures

15. The dose sits on the seam. Call caps, work per shift and context per seat stay about constant across levels.
    `solo` at `heavy` must not become a long-context test. `team-fullrecord` is the primary control for boundary
    families.
16. Exposure and announcement detection accepts a paraphrase. A measure that is constant in an arm is not
    reported for that arm.
17. Count a size cap in bytes of printable ASCII. A cap in Unicode characters lets a seat pack a compressed copy
    of the source into the note.

## The independent review (step 5)

Two reviewers work on a frozen export of the family (`git archive`), with no model call. Each writes one report
with findings ranked BLOCKER, MAJOR or MINOR; each finding has the evidence, the reason and the smallest fix.

| Reviewer | Reads first | Does |
|---|---|---|
| Robustness | The code and the builder's report | Writes 6 or more alternative scripted players and runs them in the simulator on 50 or more instances per arm and level: blind and fixed answers, guesses from visible structure, every call in unusual orders, a seat that reads all that any route gives, encodings against a size cap, stalls. Compares the built task folders of each pair. |
| Clarity | Only what a seat reads | Writes down what it would do, every sentence with 2 readings and every rule it needed and did not find. Then compares the texts with the specification and the code, plays 2 instances by hand over HTTP, and judges whether the control is clean and whether the base text already states the habit. |

The builder then resolves every BLOCKER and MAJOR finding and adds a deterministic simulation check that fails on
the first build. The reviewers run their players again on the new export.

## The live playtest (step 6)

`flotilla/playtest.py` runs a built task on the host with a real command-line assistant in each model seat,
under a sandbox profile, with the task's own world and verifier. It uses the assistant's subscription, not a
paid API key. A playtest is not an experiment: n is small and the harness is the assistant's own CLI. It
answers 4 questions before any paid run: can a strong assistant do the work from the briefing alone; where
does it lose reward and why; do the family's measures see that loss; what breaks in the tools.

18. Where a scripted seat must understand a model seat, give them a typed call. Keep free text between live
    seats. A scripted seat that cannot read a direct message asks back; it never stays silent and never guesses.
19. Write every call in full at the place where the seat needs it, with a real value. A placeholder next to a
    literal word is read as a placeholder: 8 of 20 live seats sent `until=<round number>` for
    `until=tick&after=...`, and 2 of them looped on the error until the world froze.
20. The seat's shell must work like an ordinary shell (`python3`, here-documents, a writable temp folder). A
    broken tool changes how an assistant works, and so it changes what the task measures. Run
    `playtest.py --check-seat` before a session.
21. Read the seat logs for slips that cost no reward. Then decide whether the task should score them. Two
    families got their real dose this way: a relay that renumbers or reorders the rules in its note (`digest`),
    and a seat's own script that hides the reply in which an update arrives (`commit`).
22. One seat's refusal or tool misuse can end a whole team. Record it per trial as a valid flagged result with
    its cause, and report the share of teams lost to each cause next to the reward.
23. Freeze the tool and the tasks for a session (`git archive` to a scratch folder) and record the commit.
24. A family that 2 frontier assistants pass in every live trial at `standard` and `heavy` is at the ceiling
    for that class. Keep it as a canary, or move the load to where the logs showed slips. Do not add a trick.
25. The implicit twin. A frontier assistant carries what it is told to carry. What it can miss is what the
    other seat will not have. So when a family is at the ceiling, add a twin arm that leaves out one stated
    fact about the other seat's view, and change nothing else. The fact must stay within the team's reach:
    the seat can see it in its own view, or one seat is told it and has to pass it on. The pair `team` /
    `team-implicit` measures the cost of that inference; `hint` minus `team` measures the habit. First case,
    `handoff`: without the sentence "Once a batch is settled, it leaves the desk; no later shift can see its
    lot list on the desk", Claude Opus 5.5 lost the ticket in 10 of 25 live playtests on 16 instances; with
    it, it lost 0 of 17 on 14 of those instances. GPT-6.2 Sol lost 0 at the seam in both arms. Counter-case,
    `digest`: there the left-out usage rule is written in the planner's manual, every planner copied it into
    its note, and the twin showed no loss in 16 playtests. The twin bites when the fact is written nowhere and
    the seat must infer it from its own view. These are playtests, not an experiment.

## What the first reviews found (2026-11-25)

| Family | Finding | Rule |
|---|---|---|
| `handoff` | The probe score depended on the note's syntax, because a scripted successor used one pattern | 1 |
| `handoff` | The size of a batch predicted its result, so a guess beat `naive` | 5 |
| `commit` | The base briefing told the seat to read each reply, so the family measured instruction following | 11 |
| `commit` | A desk that read no message scored 44% to 50% from the lane table | 5 |
| `interface` | The scripted desk answered any message with the exact key form and unit | 3 |
| `interface` | The world refused an order before its catalogue, which removed the coordination problem in `team` | 4 |
| `digest` | A relay that copied the note removed the effect of more hops | 15 |
| `digest` | A compressed copy of the manual fitted the cap in Unicode characters | 17 |
| `week` | One carrier satisfied every rule, so a silent team scored 0.84 | 5 |
| `week`, `digest` | A play that ran out of time was dropped as invalid, which hides the weakest teams | 9, 14 |
