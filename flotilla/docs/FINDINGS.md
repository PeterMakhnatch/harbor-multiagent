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
