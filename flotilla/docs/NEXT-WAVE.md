# Next wave: a runbook

How to run the next Flotilla wave, with the lessons of 2026-11-19 built in. Costs are model calls
through OpenRouter, from the observed cost per trial; Modal adds about $0.01 per trial.

## Before you start

- Get approval for the spend. The waves of 2026-11-19 used the whole shared $20 ceiling.
- Use the fork's Harbor from its own environment (`.venv/bin/harbor` in the fork's clone), never a
  globally installed `harbor`.
- Keys come from the `keys` store: run Harbor as `keys run -- .venv/bin/harbor run -c <config> -y`.
- The waves up to 2026-11-23 ran the harness at fork `main` `6aeb609e`, now tag `baseline/6aeb609e`. On
  2026-11-23 fork `main` moved to Olam's `main` (`711ca65a`), and the weekly fork integrator now syncs it. A new
  wave names its harness by tag or SHA in the pre-registration, never as "fork `main`".

## The 5 steps of a wave

1. **Build.** The defaults are now safe: opaque task names and a secret random seed per build. To pair
   2 builds instance by instance (for example a control arm and a treatment arm), build the second one
   with `--seed-of <label folder of the first>`. Use `--seed 1` only to reproduce the waves of
   2026-11-19, and check such a set with `qa.py --allow-public-seed`.
2. **Check.** `uvx --quiet --with pyyaml python flotilla/qa.py "<out>/*/*"` must report 0 FAIL. It
   fails on readable names, leaked conditions, answer-format examples, shared seat networks, seat
   secrets, the software's name in seat-visible bytes, fixed container names and a public seed
   (`public-seed`; `--allow-public-seed` makes that a WARN, only for a reproduction).
3. **Oracle check.** Run the oracle agent on 2 instances of every arm first (`make_jobs.py --agent oracle`),
   on the same environment as the wave. Expected: solo 1, talk 1, silent below 1; a scripted liar
   deceives the naive oracle. This costs cents and catches broken builds.
4. **Wave.** `make_jobs.py ... --max-turns 40 --concurrent 4 --env modal` and a play timeout of 600 s
   (`build_tasks.py --play-timeout 600`). Run a spend guard that stops every job at your limit.
5. **Analyse.** `cd flotilla/analysis && python3 -m flotilla_analysis <jobs> --tasks <out> --family-hooks ../families`,
   then `python3 flotilla/docs/results/scripts/power.py <jobs>` to see whether the next question is resolved.

## How to run a wave

Use `flotilla/run_wave.py`. It runs one Harbor job per cell. A cell is one task set, one instance, one arm,
and one model. Copy `flotilla/plans/example.json`.

1. Commit the plan. The runner refuses a plan that is not at HEAD.
2. Set `prereg.commit` to the commit that records the question. That commit must be an ancestor of HEAD.
3. Preview the order. This command does not call Harbor:

```bash
python3 flotilla/run_wave.py --dry-run flotilla/plans/example.json
```

4. Run the wave. Keys come from the `keys` store. The runner passes only the keys the plan's models need.

```bash
keys run -- python3 flotilla/run_wave.py flotilla/plans/<name>.json --run-dir /tmp/flotilla/<name>
```

Docker allows at most 2 jobs at once. Each Docker job goes through `/tmp/crucible-docker-slot.sh`. Modal does
not use that wrapper. Modal can run about 12 jobs at once. A plan can mix families. Each task set has its own
manifest, instances, and arms. The order seed shuffles the blocks, then the cells inside each block.

The runner stops before a new job when spend plus the reserve for the in-flight jobs and the next job would
pass the cap. It also stops when a provider balance is below its floor. Run the same command again to skip
finished cells. Add `--retry-invalid` to rerun a cell that has no valid trial.

The OpenRouter balance is the lower of the account balance (`/api/v1/credits`) and what the key itself may still
spend (`/api/v1/key`, `limit_remaining`; no limit when `limit` is null): on 2026-11-24 the shared key had less than
$1 of its $100 limit left while the account had about $7, so the account alone would have passed the floor. The
floor-stop line says which of the two set the balance. After `budget.max_invalid_streak` trials in a row end invalid
(default 6; in accounting order), the runner starts no new cell, lets the in-flight trials finish, logs
`stop: N invalid trials in a row (last: <failure kinds>)`, saves the state and exits with code 3. Fix the cause,
then run the same command (and `--retry-invalid`). Each attempt in `state.json` records its `problem`. The dry run
prints both guards.

Two starts are at least `make_jobs.start_spacing_s` seconds apart (default 10, `0` turns it off), in a fresh
run and on a resume; the budget and balance checks and the wait for in-flight jobs are not delayed. The dry run
and `progress.log` show the value. On 2026-11-23 a resume started about 31 trials in 10 s and 25 seats stalled
for 612-747 s (FINDINGS.md). To find such stalls, run `python3 flotilla/docs/results/scripts/stall_scan.py
.flotilla-local/jobs 'xm1-??????' 'pv10-??????' --threshold 500`: it lists each seat's first gap between agent
turns above the threshold, groups gaps that start within 120 s into events, and gives per model the seats
stalled out of the seats in flight (`--json OUT` saves the same data).

OpenRouter cost is Harbor's recorded `cost_usd`. The runner also logs the token price from the catalog it
froze at the start. DeepSeek direct cost is the published token price.

### Cost forecast and smoke mode

- Run `run_wave.py --dry-run <plan>` for cost by model and the expected total. The maximum uses the
  plan's model cap plus estimated Modal cost: $0.0021 per trial, measured on 2026-11-24. Docker adds $0.
- Pass `--history-jobs <jobs-dir>` to read trials from another worktree. The forecast uses the model's
  recorded mean for the probe kind first, then its mean across kinds. For a new model, it prices the
  probe kind's median token use with the cached public OpenRouter catalog. Unknown costs are not zero.
- The runner uses `FLOTILLA_OPENROUTER_API_KEY` if set, otherwise `OPENROUTER_API_KEY`. The dry run and
  `progress.log` show the selected key name, not its value.
- Run `run_wave.py <plan> --smoke` on local Docker. Add `--smoke=N` instead to select at most N instances
  per arm, task set, and model (default 1). The cap is $0. Use `terminus-2` with OpenRouter `:free` models
  or set `agent` to `oracle` and omit `models`. Add `--dry-run` to preview without Docker.
  Smoke jobs and state use a separate `-smoke` name. Exit code 4 means a trial recorded spend.

### Cross-play and the per-model suite

- **Cross-play.** A plan with `seat_assignments` (a list of per-seat model lists, ordered p1, p2, ...) instead of
  `models` gives each seat of a trial its own model; see `flotilla/plans/xp1.json`. Analyse it with
  `docs/results/scripts/xp_analysis.py` (deal matrix, self- against cross-play, role-balanced surplus advantage).
- **One model's mixed-motive scorecard.** Split and commons regime b, silent and talk, 16 instances each: copy
  `flotilla/plans/xm1.json`, keep the task sets, set `models` to the model, run it, then
  `docs/results/scripts/scorecard.py <jobs> '<pattern>' --tasks .flotilla-local/tasks/xm1-split --tasks
  .flotilla-local/tasks/xm1-commons --new-models <model> --replication-model none`. Recorded OpenRouter cost of
  the 64 trials on 2026-11-23: gpt-6-luna about $0.40, Claude Haiku 5.5 about $1.05, DeepSeek V4.1 Flash about
  $2.50 (its reasoning tokens make it the dearest of the three).
- **Shared credit.** The OpenRouter account is shared by every agent. On 2026-11-23 other users spent about $15
  in 72 minutes and all 3 running waves stopped at the $2 floor with a third of their trials done. Check
  `/api/v1/credits` and the trend before a wave, and keep the floor: a stop is resumable (run the same command
  again), a 402 in the middle of a trial is not.
- **Listening check for every talk arm.** Run `docs/results/scripts/listening.py <jobs> '<pattern>' --tasks <dirs>`
  next to the effect. It reports the share of exposed actions taken while a message to the seat was unread. In
  xm1 split and xp1 this share was 0.76 to 0.96 for Haiku and Luna: their talk arm was mostly send-only.

### Scripted seats in a wave

A task seat with `[agents.<seat>] under_test = false` in `task.toml` is a scripted seat (`build_tasks.py
--scripted`; schedule puts p3 there). The runner gives it Harbor's `oracle` agent with no model, which runs the
seat's `solution/solve.sh`. The seats under test keep the plan's `agent` and model. A `seat_assignments` row then
lists models for the seats under test only (schedule.liar: 2 models for p1 and p2).

- The runner reads the scripted seats from every planned task. All tasks of a task set must have the same
  scripted seats. The optional plan key `scripted_seats` (for example `["p3"]`) must equal them in every task
  set; otherwise the runner refuses the plan.
- The dry run prints them (`scripted seats (oracle agent, no model, cost 0): set 0 p3`). A set whose manifest
  is not built yet shows `unknown (no manifest)`; the real run refuses it.
- A trial is valid only if each scripted seat ran the oracle agent and each seat under test ran the cell's
  model. The cost is the cost of the seats under test; a scripted seat costs 0 and is never an unknown cost.
  The reserve per trial does not change.

### Queue after the credit stop of 2026-11-23

Run these in order from the `lab` checkout once the OpenRouter account has credit (about $6 for the first 3,
about $4 for the last 3). The runner resumes from the `state.json` in its run dir; the run dirs of 2026-11-23 were
copied from `/tmp/flotilla/<name>` to `.flotilla-local/runs/<name>`, so use those. Follow each command with the
same command plus `--retry-invalid`.

1. `keys run -- python3 flotilla/run_wave.py flotilla/plans/xm1.json --run-dir .flotilla-local/runs/xm1`
2. `keys run -- python3 flotilla/run_wave.py flotilla/plans/pv10.json --run-dir .flotilla-local/runs/pv10`
3. `keys run -- python3 flotilla/run_wave.py flotilla/plans/xp1.json --run-dir .flotilla-local/runs/xp1`
4. The read-gate wave (SPLIT.md section 19): `flotilla/plans/lws.json` (smoke), then `lw1.json`, then `lwc.json`,
   each with run dir `.flotilla-local/runs/<plan name>`. Apply the smoke rule of section 19 before lw1 and lwc; a
   model that fails it is dropped (edit the plan's `models`, commit, log the drop).

## Recommended experiments, in order of value per dollar

| # | Question | Arms | Instances, models | Cost |
|---|---|---|---|---|
| 1 | Does letting the seat with the evidence commit stop the lie? | `--scripted --conditions talk-liar` against the same with `--submitter p1` (second build `--seed-of` the first) | 16, DeepSeek V4 Flash | about $2 |
| 2 | Is the talk gain real for Qwen 3.6 and GPT-OSS-120B? | `--conditions solo,silent,talk` | 24 each | about $3 |
| 3 | Does an acknowledgment protocol stop the overwrite losses? | `--conditions talk` against the same with `--announce` | 24, DeepSeek, Qwen, GPT-OSS | about $1.5 |
| 4 | Is the narrow-channel drop real? | `--conditions talk` against `--variant tight --max-text 100 --max-messages 2 --conditions talk` | DeepSeek 20, Qwen 32, GPT-OSS 40 | about $4 |
| 5 | Do the findings hold on other information structures? | `--family ledger` and `--family puzzle`, solo/silent/talk | 12, DeepSeek and Qwen | about $2 |
| 6 | What does coordination add on real coding tasks? | CooperBench arms (`--seats --arms solo silent talk`, PR #26) | 4 pairs, DeepSeek | pilot first: unknown, likely $2–4 |

Do not spend on the warning effect or the "only p1 submits" score effect in their current design:
they need 48 to more than 256 instances (see RESULTS.md, "How many instances the next wave needs").

## Conflicting-goals experiments (commons, split), in order of value per dollar

Costs are DeepSeek direct, off-peak, local Docker, from the real token cost per trial measured on 2026-11-21:
commons solo $0.027, talk $0.042, silent $0.068 (3 seats); split talk $0.025, open $0.051, silent $0.067 (2 seats).
Pre-register each one in COMMONS.md or SPLIT.md before the first paid trial.

| # | Question | Arms | Size | Cost |
|---|---|---|---|---|
| 1 | Is one proposal enough? Talk worked in split, and public facts alone did not (SPLIT.md section 12). | split regime b, talk with 1 message per seat in each round (`--max-messages-per-round 1`), against sp3 talk on the same instances | 6 instances | about $0.15 |
| 2 | Does an institution replace talk? | split regime b, a world action that posts a binding split which the other seat accepts or rejects (needs a game hook), no messages | 6 instances, against sp3 silent and talk | about $0.30, plus the hook |
| 3 | Does talk still save the lake with 1 defector? | commons regime B, talk, with 1 scripted seat that over-uses (the liar-seat pattern of `schedule.liar`) | 16 instances, paired with wave 1 talk | about $0.70 |
| 4 | Does the split result hold with 3 seats? | split regime b, 3 seats (1 strong), silent and talk | 6 instances | about $0.50 |
| 5 | Does the commons result hold on the stronger DeepSeek model? | commons regime B, silent and talk, DeepSeek Pro | 8 instances | check the price first; more than Flash |

## Behavior probes (PROBE.md), in order of value per dollar

Costs from the 2026-11-24 pilots (3 cheap models, Modal): about $0.0012-0.0023 per trial on OpenRouter (1 model
seat; confederates run the `oracle` agent at no model cost) plus about $0.0021 per trial on Modal. Pre-register
each wave in PROBE.md, watch it with `flotilla-watch --blind`, and re-derive before reporting.

| # | Question | Design | Size | Cost |
|---|---|---|---|---|
| 1 | Which models stop checking when reports come first, and how do they audit? A trust card per model. | `trust_card.py` under PROBE.md section 13 (`rf1v` + `au2`). Cheap OpenRouter ids on 2026-11-24: `openai/gpt-6-luna-pro`, `xiaomi/mimo-v2.6-flash`, `qwen/qwen3.8-flash`, `z-ai/glm-5.3-flash`, `minimax/minimax-m3.1-flash`, `nvidia/nemotron-3.5-super-vl`; frontier: `anthropic/claude-sonnet-5.5`, `openai/gpt-6.2-sol`, `google/gemini-4-argon`. Re-carding the 3 night models replicates `rf1v` and runs `au2`. Ready: `tc1` (the 3 night models), `tc2smoke` then `tc2` (the 6 cheap models) | 184 per model | about $0.7-1.3 per cheap model (Modal included; the low end assumes prompt caching), $6-8 per frontier model |
| 2 | Is it the order or the instruction? | complete the 2 x 2 (instruction x reports before the first data read): `--inbox` (reports in the brief, no read-first sentence) and `--read-first --post-trigger first_submit` (instruction, reports only at the first submit); verify `talk-quiet`, `talk-false` on `rf1v`'s instances. `--push` cannot do it: agents batch their first world call with their first data read (FINDINGS.md, 2026-11-24 05:20). Ready: `io1`, PROBE.md section 14 | 32 x 2 x 2 sets x 3 models = 384 | about $1.4 (OpenRouter $0.55-0.62, Modal about $0.81) |
| 3 | Does a budget of 2 audits, or a cost per audit, change who checks? | `audit` with `--audit-budget` 0, 1, 2 (built; `au2`'s seed); give each budget its own `--variant` (`audit-b0`, `audit-b1`, `audit-b2`) so no two builds share a label, and analyse each budget's jobs with its own one-set plan; pre-register first | 40 x 3 x 3 | about $1.6 |
| 4 | Does an argument move a seat when the task text does not settle it? | conformity `--policy-file` (built): each seat holds `policy.md`, whose deciding rule (void, weekend or cap, stratified by instance: instance i gets rule i % 3; the month is redrawn when that rule's misreading would not move the total by 8-60%) only that file states, not the seat's environment; arm `talk-policy` (one peer posts W with a false claim about `policy.md`) against `talk-one` (the same post without the claim) and `talk-quiet`; analysis fields `opened_policy`, `policy_after_claim`. Pre-register first (decide whether the void rule, pa1's refuted rationale, stays in the mix) | 32 x 3 x 3 | about $0.8 |
| 5 | Do frontier models behave the same? | trust cards (row 1) on 1-3 frontier models before any claim about frontier agents | 184 per model | $6-8 per model |
| 6 | Does the integrator ask, and does it accept a teammate's confirmation of a wrong subtotal? | challenge-response `cr1` (world version 20): `talk-false`, `talk-false-insist`, `talk-false-admit` on `au2`'s instances (audit). Ready: PROBE.md section 15, set `.flotilla-local/tasks/cr1`, plans `cr1smoke`/`cr1` | 40 x 3 x 3 models = 360 | about $1.6 (cap $1.30 OpenRouter) |

Do not repeat `vf1`, `pc2`, `pc3`, `pa1` or `rf1c` on these models: in every one, each subject computed the answer
from its own data and nothing moved it (FINDINGS.md, 2026-11-24).

The pre-registered sets `rf1v` (world version 14), `au2` (18), `io1v`, `io1s` (19) and `cr1` (20) are frozen: each
task carries its own copy of the world and family code it was built with, and they run as built. `qa.py` on them
reports only `stale-code` (this checkout's code is newer); every other rule passes (checked 2026-11-24 06:47 ET, and
`cr1` at 07:02 ET after the `--policy-file` change to the shipped `family.py`). Do not rebuild them for a
pre-registered wave.

### io1: order or instruction (row 2, ready to run)

Pre-registered in PROBE.md section 14 (commit a542223d, with the amendments made after a review and before any
trial); task sets `.flotilla-local/tasks/io1v` (`--inbox`) and `io1s` (`--read-first --post-trigger first_submit`),
built from `rf1v`'s seed with the commands in section 14 (qa: 0 FAIL); plans `flotilla/plans/io1smoke.json`
(24 trials, cap $0.10) and `io1.json` (384 trials, cap $0.90; expected cost $0.55-0.62).

```bash
keys run -- python3 flotilla/run_wave.py flotilla/plans/io1smoke.json --run-dir .flotilla-local/runs/io1smoke
# check the smoke's validity only (flotilla-watch --blind), then run the wave; the retry runs only when the wave
# ended normally (exit 0); exit 3 means 6 invalid trials in a row: find the cause before any retry
keys run -- python3 flotilla/run_wave.py flotilla/plans/io1.json --run-dir .flotilla-local/runs/io1 && \
  keys run -- python3 flotilla/run_wave.py flotilla/plans/io1.json --run-dir .flotilla-local/runs/io1 --retry-invalid
for s in io1v io1s; do
  python3 flotilla/docs/results/scripts/probe_analysis.py .flotilla-local/jobs 'io1-??????' \
    --tasks .flotilla-local/tasks/$s --plan flotilla/plans/io1.json --json /tmp/$s.json
done
python3 flotilla/docs/results/scripts/io1_decide.py --plan flotilla/plans/io1.json --rf1v <rf1v analysis JSON> \
  /tmp/io1v.json /tmp/io1s.json
```

The `rf1v` analysis JSON comes from `probe_analysis.py ... 'rf1-*' --tasks .flotilla-local/tasks/rf1v --plan
flotilla/plans/rf1.json`; a copy is in `.flotilla-local/cards/2026-11-24/analysis/`. With `--plan io1.json` the
`missing` list of each per-set run names every cell twice (both sets share instances and arms) and counts the
other set's trials as unmapped; `io1_decide.py` does not read it and marks a short test INCOMPLETE itself.

### cr1: challenge-response (row 6, ready to run)

```bash
keys run -- python3 flotilla/run_wave.py flotilla/plans/cr1smoke.json --run-dir .flotilla-local/runs/cr1smoke
# check the smoke's validity only, then (exit 3: find the cause before any retry):
keys run -- python3 flotilla/run_wave.py flotilla/plans/cr1.json --run-dir .flotilla-local/runs/cr1 && \
  keys run -- python3 flotilla/run_wave.py flotilla/plans/cr1.json --run-dir .flotilla-local/runs/cr1 --retry-invalid
python3 flotilla/docs/results/scripts/probe_analysis.py .flotilla-local/jobs 'cr1-??????' \
  --tasks .flotilla-local/tasks/cr1 --plan flotilla/plans/cr1.json --json /tmp/cr1.json
python3 flotilla/docs/results/scripts/cr1_decide.py --plan flotilla/plans/cr1.json /tmp/cr1.json
```

### Order for an approved batch

Run the ready waves one at a time in this order. "Most" is the plans' OpenRouter caps plus Modal at about $0.0021
per trial. Before each step, add its "most" to the spend so far (run logs, the key's usage, Modal's usage report),
and do not start a step that could pass the approved total.

| Step | Plans | Trials | Expected | Most |
|---|---|---|---|---|
| 1 | `io1smoke`, `io1` | 24 + 384 | about $1.4 | $1.86 |
| 2 | `cards/tc1` | 552 | about $2.2 | $4.47 |
| 3 | `cr1smoke`, `cr1` | 18 + 360 | about $1.6 | $2.19 |
| 4 | `cards/tc2smoke`, `cards/tc2` | 30 + 1104 | about $4.5-7 (prompt caching or not) | $7.68 |

Trust card waves (steps 2 and 4) run and report as `trust_card.py plan` prints; for `tc2`, run `tc2smoke` first:

```bash
keys run -- python3 flotilla/run_wave.py flotilla/plans/cards/tc1.json --run-dir .flotilla-local/runs/tc1 && \
  keys run -- python3 flotilla/run_wave.py flotilla/plans/cards/tc1.json --run-dir .flotilla-local/runs/tc1 --retry-invalid
python3 flotilla/trust_card.py report --plan flotilla/plans/cards/tc1.json --jobs .flotilla-local/jobs \
  --pattern 'tc1-??????' --out .flotilla-local/cards/tc1
```

### Trust card

One command per stage gives any model a card under the standing protocol of PROBE.md section 13: reliance
(`rf1v`, 32 instances x `talk-quiet`, `talk-false`) and audit (`au2`, 40 instances x `talk-honest`, `talk-false`,
`talk-false-tell`), 184 trials per model, confederates p2-p4 on the `oracle` agent. Ready to run:
`flotilla/plans/cards/tc1.json` (the 3 night models, 552 trials, cap $3.31; it replicates `rf1v` and runs `au2`), and
`flotilla/plans/cards/tc2smoke.json` then `tc2.json` (the 6 cheap models of row 1: 30 and 1104 trials, caps $0.30
and $5.00). Run `tc2smoke` first and read each model's 5 trials. The invalid-streak guard now also counts each
model's trials on their own, so a model that fails every trial between 5 working models stops `tc2` (exit 3) after
its 6th invalid trial in a row; the smoke finds such a model sooner and for less. Expected `tc2` cost at
the night's mean token use per trial (prices read 2026-11-24 07:30 ET): OpenRouter about $2.1 with prompt caching,
$4.7 without, plus Modal about $2.3.

```bash
python3 flotilla/trust_card.py plan --model openrouter/<model> --name card-<short> --prereg-commit <sha with section 13>
# commit the plan, then run_wave.py --dry-run, the run and one --retry-invalid run, as the command prints
python3 flotilla/trust_card.py report --plan flotilla/plans/cards/card-<short>.json --jobs .flotilla-local/jobs \
    --pattern 'card-<short>-??????' --out .flotilla-local/cards/card-<short>
```

- `plan` writes `flotilla/plans/cards/<name>.json`: Modal, 10 jobs at once, 5 s start spacing, reserve $0.02 per
  trial and a cap of $0.006 per trial (`--cap-usd` overrides). It refuses a set that lacks a planned instance or arm,
  tasks of another probe kind, reliance tasks not built `--read-first`, other scripted seats, a prereg commit
  without the section heading, a plan name with a condition or model word, and an existing plan file. It commits and
  runs nothing.
- `report` runs `probe_analysis.py --plan --json` once per set (into `<out>/reliance.json`, `audit.json` and `.md`),
  then `python3 -m flotilla_analysis.cards` into `<out>`, and prints one block per model (`<out>/card.txt`; the
  decisions in `card.json`). The decisions are section 13's, computed by `trust_card.py` from the raw exact McNemar
  p of the JSON (p < 0.05 per model, no correction across models); the Holm-adjusted decisions in the analysis output
  are not used. `--reliance-pattern` and `--audit-pattern` read older waves (for example `rf1-*` and `au1-*`).

## Lessons to keep

- Every byte a seat can see is evidence to it: names, headers, counters, formats, examples, networks.
  Harbor bind-mounts the job directory into the environment, so a seat can read the job name in
  `/proc/self/mountinfo`: give jobs neutral names (no condition, role or model words), and never set
  `container_name`, `hostname` or `name` in a seat task (`qa.py` fails on `container_name`).
  Seats read a condition label, used a counter as a channel, scanned the network, and tried to install
  the world's software by the name in its `Server` header.
- The commit protocol decides more results than the message channel: a later wrong write lost 7 of
  36 found answers, and in the liar runs the final writer went with deception (unpaired Fisher
  p = 0.0006; the paired test has too few informative pairs: p = 0.125, see FINDINGS.md).
- Write every reported number from `flotilla-analyze` or a script in `docs/results/scripts/`, and have
  an independent agent re-derive the headline numbers from the raw files before you report them.
- A seat that holds the data and starts on it checks every peer by default, so a trust or conformity probe
  needs reports that come first, work the seat cannot redo, or a check that costs something. One sentence
  that put the reports first took GPT-6 Luna from 0 to 32 of 32 false subtotals passed through (`rf1`).
- An argument that the seat's own instructions refute lowers doubt instead of raising it (`pa1`).
