# Next wave: a runbook

How to run the next Flotilla wave, with the lessons of 2026-11-19 built in. Costs are model calls
through OpenRouter, from the observed cost per trial; Modal adds about $0.01 per trial.

## Before you start

- Get approval for the spend. The waves of 2026-11-19 used the whole shared $20 ceiling.
- Use the fork's Harbor from its own environment (`.venv/bin/harbor` in the fork's clone), never a
  globally installed `harbor`.
- Keys come from the `keys` store: run Harbor as `keys run -- .venv/bin/harbor run -c <config> -y`.

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

OpenRouter cost is Harbor's recorded `cost_usd`. The runner also logs the token price from the catalog it
froze at the start. DeepSeek direct cost is the published token price.

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
