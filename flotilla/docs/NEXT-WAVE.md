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

## Lessons to keep

- Every byte a seat can see is evidence to it: names, headers, counters, formats, examples, networks.
  Harbor bind-mounts the job directory into the environment, so a seat can read the job name in
  `/proc/self/mountinfo`: give jobs neutral names (no condition, role or model words), and never set
  `container_name`, `hostname` or `name` in a seat task (`qa.py` fails on `container_name`).
  Seats read a condition label, used a counter as a channel, scanned the network, and tried to install
  the world's software by the name in its `Server` header.
- The commit protocol decides more results than the message channel: a later wrong write lost 7 of
  36 found answers, and in the liar runs the final writer predicted deception (p = 0.0006).
- Write every reported number from `flotilla-analyze` or a script in `docs/results/scripts/`, and have
  an independent agent re-derive the headline numbers from the raw files before you report them.
