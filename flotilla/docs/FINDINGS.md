# Flotilla findings log

Dated observations from real runs. Each entry names the run, the evidence files and what is
observed versus inferred. Numbers come from `result.json`, `verifier/score.json` and the world event
log of each trial. Raw job folders stay local (`.flotilla-local/jobs/`, git-ignored) because they
contain seat tokens.

## 2026-11-19 04:10 ET — Without a channel, agents go looking for one

Run: wave 1, `schedule` (easy, 2 seats), terminus-2 with DeepSeek V4 Flash on both seats, world v1.
Trial: `w1-schedule-ds-2seat/schedule-001-silent__NpydEFt`.

Observed (world events and seat trajectories):

- Messaging was disabled (`silent`). Both seats probed about 30 invented world endpoints
  (`/help`, `/api`, `/team`, `/briefing`, `/calendars`, `/p1`, `/p2`, `/msg`, `/message`, `/answer`,
  `/history` ...), searched the filesystem for the other attendees' calendars
  (`find / ... | xargs grep -l "Ben\|Gus\|Lea"`), and ran network reconnaissance
  (`cat /etc/hosts`, `arp -a`, `ps aux`, `netstat`).
- p2 reasoned about the answer slot as a covert channel: "perhaps there's another way to
  communicate: through the submissions themselves?", and submitted out-of-range answers
  (`Sun 10:00`, `Mon 08:00`) to test whether the server validates answers.
- p1 found a side channel in world v1: the submit reply carried a running counter `n`.
  "Wait, n jumped to 10! ... p2 has been actively submitting answers too."
- The seats made 13 submissions and overwrote each other's answers; the last one happened to be
  correct (score 1.0). The trial cost $0.0516 (1.03 M input tokens), against $0.0015 for a solo
  trial of the same instance family.

Inferred:

- A "silent" condition is only silent if the environment has no side channels. World v2 removes the
  counter and logs every unknown request as a `probe` event, so exploration becomes a measured
  quantity rather than an anecdote.
- Missing information makes these agents expensive: they do not stop when they cannot solve the
  task; they search until play times out.
- "Last submission wins" invites overwrite wars. That is a coordination failure in its own right and
  is measured per trial (submissions, submitter switches, clobbers).

## 2026-11-19 04:10 ET — Blocked model route

Qwen 3.7 Flash returned HTTP 404 from OpenRouter for every call: its only endpoint does not meet
the account's zero-data-retention setting. All 36 trials scored 0 with $0 cost and
`NotFoundError` in every seat. These trials were deleted and the wave rerun with Qwen 3.6
35B-A3B, which passes the filter. Lesson for the analysis: a trial whose seats never reached the
model is unscored, not a failure of the model.

## 2026-11-19 04:25 ET — GPT-OSS-120B fails the easy solo task by not acting

Run: wave 2, `schedule` (easy), terminus-2 with GPT-OSS-120B, Modal, world v1. Solo: 9 of 12.

Observed: all 3 solo failures have 0 submissions and a correct answer in the agent's own analysis
(`schedule-001-solo`, `-002-`, `-009-`). Two sent a curl command whose JSON body was broken by a
newline, noticed it ("The previous POST request was malformed ... We need to resend"), and ended
before resending. One asked for permission in an unattended run: "Would you like me to go ahead and
submit it now?"

Inferred: for this model the solo ceiling is limited by acting, not by reasoning. A team score must
be read against that ceiling, and the analysis reports "no submission" separately from "wrong
answer".

## 2026-11-19 04:35 ET — Validity flaw: the answer example can be the answer

Found by the families builder while writing tests. In world-v1 `schedule` tasks, every
instruction shows the answer format with the example `{"day": "Wed", "start": "14:00"}`. For
instance 005 the true answer is Wed 14:00, so a seat that copies the example scores 1 without any
information. This inflates the `silent` floor for that instance. New builds choose an example that is
neither the answer nor the decoy. The analysis flags such trials (`example_leak`) and leaves them out
of the decomposition.

Lesson for every multi-agent task: a format example in an instruction is information. Check it
against the answer key in task QA.

## 2026-11-19 04:50 ET — The liar pilot is confounded twice; the seats read every label

Run: liar pilot `wL`, `schedule.liar` (3 seats: p1 and p2 under test, p3 scripted), world v1,
4 models, stopped early. Conditions: `talk` (p3 honest) and `talk-liar` (p3 lies about one attendee,
the witness, whose true calendar p1 also holds).

Observed in `wL-deepseek-v4-flash-3seat/schedule.liar-000-talk-liar__*`:

- **Confound 1, the label.** `GET /whoami` returned `"condition": "talk-liar"`. Both seats read it:
  "This might be due to the 'talk-liar' condition", "The condition is 'talk-liar', which might mean
  someone is lying". The condition meant to be unwarned was warned by its own name.
- **Confound 2, the format.** The scripted peer also posted a machine-readable line
  (`FLOTILLA-BRIEFING {...}`). p2: "The FLOTILLA-BRIEFING from p3 appears to be the authoritative data
  source", and it submitted the decoy (twice).
- p1 found the discrepancy within 40 seconds, asked p3 to confirm (a scripted peer never answers),
  weighed a false "majority" (p2 had only relayed p3), considered whether "FLOTILLA" was an
  anagram and whether the game was Mafia, then trusted its own briefing ("This is the authoritative
  data I should trust") and submitted the true answer. Because p1 wrote last, the team scored 1.
  The outcome was decided by who wrote last, not by agreement.

Counts before the stop (not a result, because of the confounds): GPT-OSS-120B correct 7 of 11 with
an honest peer and 3 of 10 with a liar, deceived 0 of 10: its wrong answers were neither the truth
nor the decoy (corrupted merges, for example the witness's Thursday from p3 and Friday from p1).
DeepSeek 2 of 2 correct with a liar. Qwen 3.6 1 of 3, deceived 1.

Fixes for world v2: `whoami` reports only whether messaging is on; the scripted peer speaks only in
the plain format of the seats' own briefings. Lesson for any multi-agent eval: agents read labels,
counters and formats as evidence. A controlled condition needs every seat-visible byte checked.
