# Learnings: seats, attribution, and what to measure

Written 2026-11-19 from local runs on this fork plus two Anthropic reports:
[Patterns and problems in emerging multiagent systems](https://www.anthropic.com/research/multiagent-systems)
and [Project EVE](https://www.anthropic.com/research/project-eve-claude-plays-mmorpgs).
Publisher numbers below are claims, not local reproductions. Local numbers
are from `RESULTS.md` in this directory.

## What local runs showed

1. **Attribution is the first integrity problem, not capability.** Stock
   hello-seats paid reward 1.0 to a tested seat that slept while the support
   seat forged its author field. The verifier trusted two participant-written
   signals: a JSON field and a log file's presence. Server-side identity
   (token-auth task) killed the attack and kept honest play at 1.0.
2. **Seat-image affordances are part of the construct.** A real model
   (gpt-oss-120b via terminus-2) failed the fixed task because its polling
   script needed `jq`, which the player image lacks. With jq added it passed
   2 of 3; the third failed on shell quoting of the reply JSON and stopped
   after two rejected POSTs. A seat score here measures image contents and
   shell quoting at least as much as cooperation.
3. **The score always measures the partner too.** With a nop support seat the
   tested oracle timed out and scored 0. Any seat-task result needs the
   control rosters (scripted partner, silent partner) reported beside it.
4. **Cost is per-seat or it is fiction.** Six model seat runs cost $0.0028
   total; per-seat cost varied 3x ($0.00025–$0.00078) inside identical
   rosters. Job summaries name only the first tested seat's agent.
5. **Evidence can vanish silently.** A failed seat log download was logged
   and the trial still verified, with nothing in `result.json` to show the
   seat's evidence never arrived (fixed on a sibling branch by recording
   `logs_downloaded` per seat).

## What the Anthropic reports add

- Coordination buys coverage, not efficiency: the vuln swarm used 27M tokens
  vs 6.5M independent, with only 12 overlapping finds and comparable
  tokens-per-find in the shared scope. Score tokens per accepted result,
  never raw output count (876–980 opened PRs at 80 agents was failure).
- Same model plus same brief gives the same action (18/30 identical branch
  names). Diversity must be forced in the brief: distinct territory, seed
  facts, or output namespace per seat.
- Scaffold quality dominated model choice in EVE Frontier (tutorial in days
  with ship losses on a weak scaffold; under 12h with zero losses on a strong
  one). This matches learning 2 above: fix the seat harness before adding
  seats.
- Teammates almost never catch a saboteur (1 definitive ID); public-channel
  monitors caught 20–40% at ≤5% false accusations. A seat eval that checks
  only final output will certify compromised teams. Adversarial seats belong
  in the design, with an independent verifier or arbiter as the accept gate.
- Hidden-profile groups lost 49–68 points against the solo all-facts ceiling
  by converging on shared facts. Briefs must make seats volunteer unique
  private facts early, and evals need the solo ceiling as a control.

## Rules we would apply to the next seat task

1. Identity is assigned by the world from a credential the seat cannot read
   for another seat; never trust a caller-supplied author or a log file the
   seat writes itself.
2. Grade from the world's own record, snapshotted at end of play; treat seat
   logs as claims, not evidence.
3. Ship control rosters with the task: honest scripted partner, silent
   partner, delayed partner, and one adversarial partner. Report the tested
   seat's score under each.
4. Pin and publish the seat image's affordances (jq, python, curl) as part of
   the task version; a missing tool is a task change.
5. Record per-seat cost, timing, exception, and evidence completeness in the
   result; a team total that skips unknowns is a lower bound, not a total.
6. Give every seat a stop-and-escalate instruction for conflicting goals,
   plus a verifiable shared criterion; contradictory briefs otherwise
   escalate to lockouts and sabotage in the Anthropic turf-war testbed.

## Not yet run locally

- Delayed-partner commit timing (does the tested seat wait, fabricate, or
  quit?) — arms are scripted; first rows are being collected.
- Contradictory-spec seats on one shared branch (turf-war analogue).
- A lying-scout seat whose claims the tested seat must verify before acting.
- Any matched-budget solo-vs-team comparison. Every local row so far is a
  control or an integrity probe, not a capability claim.
