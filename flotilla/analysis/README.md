# flotilla-analysis

Reads harbor-multiagent job folders produced by Flotilla tasks and answers three questions for each
task label and model team: did the team gain from coordination, what did it cost, and did the seats
use their messages? Standard library only (Python 3.11+); the same inputs and seed give the same bytes.

```bash
cd flotilla/analysis
python3 -m flotilla_analysis ../../.flotilla-local/jobs                  # text
python3 -m flotilla_analysis JOBS --format markdown --output report.md
python3 -m flotilla_analysis JOBS --format json --seed 1                 # every number, per trial too
python3 -m flotilla_analysis JOBS --tasks TASKS --family-hooks ../families  # text hooks for old task folders
pip install .   # optional: installs the `flotilla-analyze` command
```

`PATH` is a trial folder, a job folder, or any folder that holds job folders. The walk stops at the
first folder whose `result.json` has `trial_name` (as Sextant does) and never follows directory links.
`--tasks DIR` names where to find task folders when the path in `result.json` no longer exists.
`--manifest FILE` names a build manifest (`manifest.json` at the root of a `build_tasks.py` output) for
opaque task names whose task folder is gone.

Analyze one compatible run set at a time. `--allow-mixed-builds` permits pooling when the build
checks report a difference. The report keeps a warning that names the differing fields.

`--family-hooks DIR` (for example `flotilla/families`) supplies the family modules whose analysis hooks
read message texts (see "Found and lost answers"); task folders built before a hook existed lack it.

## Inputs

Per trial: `result.json` (task name, task path, `seat_results`, reward, `reward_valid`, timings),
`artifacts/logs/world/events.jsonl` and `messages.jsonl`, `verifier/score.json` and
`verifier/world-state.json`, and `seats/<seat>/agent/trajectory.json` (ATIF). Each trial needs its task's
label, instance and condition (`flotilla_analysis/names.py`). A readable task name says them:
`^flotilla/(<label>)-(<iii>)-(solo|silent|talk|talk-liar|talk-warned|talk-liar-warned)$`, the name of
every task built before opaque names. An opaque name (`flotilla/t-<12 hex>`, the default of
`build_tasks.py`) does not; they come from `task.toml` `[metadata]` in the trial's task folder (the path
in `result.json`) when that folder still holds the task of that name, else from a build manifest:
`--manifest FILE`, `DIR/manifest.json` of `--tasks DIR`, or the manifest at the build root of the task
folder. A trial whose task none of them names is reported as a problem. `<label>` is `<family>` or
`<family>.<variant>`, and labels are never pooled. `world-state.json` is authoritative for messages and
submissions (the verifier wrote it after freezing the world; the JSONL artifacts were collected
before verification); differences are reported per trial.

The answer-dependent measures (clobbers, invalid answers, the acted-on proxy, found and lost answers)
also need the task folder: `environment/task.json` (answer key), `environment/family.py` and the seats'
`SEAT_BRIEFING` in `environment/docker-compose.yaml`. A task folder is used only if its `task.json` names
the same task and the family scorer gives the final answer the score the verifier recorded; otherwise
those measures are `unknown`. A task folder rebuilt since the run has another opaque name: the build
manifest (at a `--tasks` root, a `--manifest` root, or the build root of the task path) lists it under
the same label, instance and condition. The tool imports `family.py` from the task folder (and, with
`--family-hooks DIR`, `DIR/<family>.py`): run it on folders you built.

The score check alone cannot tell a folder of another build when the final answer was wrong, and
those are the trials that found, lost and clobbered answers are about. So a folder that is not
provably the one the trial ran (every folder of a readable name, and an opaque name's folder that was
rebuilt or moved) must also match the run, else it is unusable with the problem "task folder rebuilt
with different parameters (<what differs>)", and the details are listed per trial:

- the seats and the message limits that the world recorded (`world-state.json` or `score.json` seats,
  the limits in the `start` event);
- for each seat under test whose trajectory shows its instruction (terminus-2 puts it in the first user
  message), the folder's `instructions/<seat>.md` up to its "Answer format" section, compared with
  whitespace collapsed (the example-leak check reads the answer format from the trajectory, so a later
  fix of a family's example does not hide an old leak);
- when the run's build is known (its own folder's `task.toml`, or for an opaque name the manifest entry
  of that name, which `build_tasks.py` writes with `build`: seed, difficulty, `n_seats`, `team_seats`,
  `max_text`, `max_messages`, `scripted_seats`, `submitter`, `announce`, and `instructions_sha256`):
  every build parameter, and the sha256 of each instruction file not checked by its trajectory.

A readable name's manifest entry is not used (a later build of the same name replaces it), and a
manifest written before build parameters existed gives none: then only the world record and the
trajectories are compared.

Each build draws a secret seed (CONTRACT.md 2), so a rebuild without `--seed` or `--seed-of` holds other
instances and differs in `seed`. To read jobs of a build whose folders are gone, rebuild with its seed:
`--seed 1` for every build before secret seeds, else `--seed-of` an archived folder or the seed in the
manifest entry's `build` record.

## Outputs and rules

Missing is not zero: a value a file does not record is `n/a` (JSON `null`) and is left out of means.

**Build checks** per label and team: build settings come from the manifest entry or the trial's
`task.toml` and `environment/task.json`. The check also compares the world version and limits
recorded during the trial. The analysis refuses to pool differing recorded settings,
including seeds, difficulty, world version, limits and scripted roles. Only the actual seat count
is checked separately for solo and team trials. The report lists counts of trials with an unknown
build. A group of only unknown builds is accepted unless its known world settings differ. A group
with known and unknown builds requires `--allow-mixed-builds`. No report prints build seeds.
A readable task name can be reused. Its original path must still hold the task files from the run;
an archive that lacks those files cannot verify the build settings.

**Scores** per label, team and condition: instances, scored trials, unscored trials (not finished, no
reward, non-finite or out-of-range reward, or `reward_valid` false) with reasons. The mean is over
per-instance mean rewards, with a 95% Student t interval over instances (df = instances − 1),
clipped to [0, 1].
An interval of zero width (every instance equal) is flagged: it understates the uncertainty.
The same mean is also given without trials flagged `example_leak` (below).
The checked reward also determines final correctness for the acted-on proxy, scripted-peer outcomes
and found/lost outcomes. Non-finite or out-of-range `score.json` scores are counted as missing.
Invalid scores and conflicts with a checked reward are reported per trial.

**Validity flag `example_leak`** per trial: true when the answer key is one of the answer examples
shown to the seats: each JSON code span in the "Answer format" section of the instruction a seat saw
(the first messages of its trajectory; without a trajectory, `<task>/instructions/<seat>.md`) is scored
against the key with the family scorer, and a full score is a leak (a seat that copies the example is
right with no information; world-v1 `schedule` shows `{"day": "Wed", "start": "14:00"}`, the answer of
instance 005). Flagged trials stay in the scores, are counted there, and are left out of the
decomposition unless `--keep-leaks` is given; the output says how many were left out.

**Decomposition** per label and team, over instances scored in all of solo, silent and talk:
ceiling = solo, floor = silent, team = talk, gap = solo − silent, gain = talk − silent (paired t
intervals), a two-sided paired sign-flip p for the gain (exact enumeration up to 16 nonzero
differences, else 20,000 draws, p = (1 + extreme) / (1 + draws)), and recovery = mean(gain) / mean(gap)
with a 10,000-resample percentile bootstrap over instances. Recovery is undefined when the mean gap
is below 0.05 reward points on the [0, 1] scale, including any nonpositive gap. The report shows
the gap and its paired 95% t interval next to recovery. The bootstrap interval is undefined if
more than 2.5% of resamples have a gap below 0.05; otherwise the interval uses the 2.5th and 97.5th
percentiles of the defined resamples (Hyndman–Fan type 7) and reports the number left out. Draws
come from `random.Random("<seed>/<label>/<team>/<purpose>")`.

Each decomposition row uses the same paired instance set. The report gives its size and marks
conditions whose score levels use a different set. The scripted-peer effects give the pair count
for each effect and mark any difference from its condition levels. A two-sided sign-flip test
with 5 or fewer nonzero pairs cannot reach p ≤ 0.05. The report shows the smallest achievable p.

Intervals account for instance variation, not job-level clustering. Solo runs in a separate job.
The intervals do not measure variation between jobs.

**Cost** per condition: input tokens (cache included, Harbor's convention), cached and output tokens
and USD per trial, summed over the seats under test (a scripted seat is not part of the team). A trial
with any seat's value missing is incomplete and left out of the means. Ratios talk/solo and
silent/solo are ratios of mean per-instance values over instances complete in both conditions.

**Message funnel** per trial and condition, over (message, recipient) pairs. Recipients are the
other seats under test for a message to `all`, else the named seat. *Delivered*: the message id is in
`data.ids` of a `deliver` event of the recipient. *Seen*: delivered, and a distinctive probe of the
message appears in the observation of a recipient step that did not end before the delivery could
have happened.

If `events.jsonl` is missing, delivery and sight are both `unknown`, not `no`.

To check sight:

- probes are all 24-character windows of the text with whitespace removed, in three forms: raw,
  JSON-escaped with ASCII escapes (the world's reply) and without (jq); removing whitespace makes
  terminal wrapping and pretty-printing irrelevant; a message without a probe is `unknown`;
- a probe does not count when it also occurs in another message with a different text, in the
  recipient's instruction or own briefing, in the recipient's keystrokes so far, or in its terminal
  output from steps that ended before the delivery;
- timing: the world clock starts no earlier than `environment_setup.started_at` and no earlier than
  (a seat's agent start − the world time of that seat's first request); a delivery at world time t is
  no earlier than that bound + t; a step ends at its ATIF timestamp (terminus-2 records a step after
  capturing its terminal output).

*Acted on* (generic proxy, two or more seats): `yes` when the final answer is correct and the seat that
submitted it could not answer alone (`oracle_answer([own briefing])` does not score 1); `no` when the
final answer is wrong or missing; `n.a.` when the submitter's own briefing already gives the answer;
`unknown` when the checked reward or submission record is missing, or when the proxy needs a task
folder and none is usable. In `silent` the same count measures lucky answers: it is the baseline
for `talk`. For `schedule`, `oracle_answer` takes the first window that fits, so a briefing
can "give the answer" by luck; the report counts such briefings ("Own-briefing check").

**Scripted-peer tasks** (a `decoy` in `task.json`, e.g. label `schedule.liar` with an honest scripted
peer in `talk` and a lying one in `talk-liar`): per condition, how many final answers are correct,
deceived (the final answer is the decoy; the verifier's `deceived` in `score.json` when present) or
corrupted (neither, including no answer).

**Answer churn** per trial: submissions, distinct answers, answer changes, overwrites (a change by a
different seat than the previous submitter), invalid submissions (not a well-formed answer: the family
may define `valid_answer(answer)`; otherwise the answer must be a non-empty object with every key of
the answer key, and for `schedule` its (day, start) must be one of `windows()`), whether a correct
answer was ever submitted, clobbers (a correct standing answer replaced by a wrong one), the last
submitter, and the time of the first message (world seconds).

When the frozen state lacks submissions and the event log only records an answer prefix, the
report marks that answer unknown. The submission still counts.

**Found and lost answers** per talk trial (conditions `talk*`), and counted per label, team and
condition (won = found and final answer right):

- *found*: some submission scores full with the family scorer; *final_right*: the checked reward is
  full; *lost*: found and the checked reward is below full. The *solver* is the seat of the first
  right submission (world submission order).
- *teammate_overwrite*: after the first right submission, another seat submitted a wrong answer (the
  first such seat is the *overwriter*). *later_by_solver* and *later_by_others* count every submission
  after the first right one.
- *solver_had_teammate_data*: at or before the world time of its first right submission, the solver
  fetched (a `deliver` event) a message from another seat that carries that seat's own information.
  *teammate_had_solver_data*: the same for the overwriter and the solver's messages, before the overwrite.
  "Carries its own information" is the family hook `carries_own_data(text, private)`, with the sender's
  briefing as `private` (a scripted peer's posted part). A bare day-and-hour regex is not used: it also
  matches format examples inside requests ("Please send Ben's hours (e.g. Mon 09,10)").
- *solver_announced*: the solver sent a message that names the right answer (family hook
  `answer_in_text(text, key)`) at most 10 s before its first right submission, or any time after it.
  Seats often post "the slot is X, submitting now" and submit on their next turn: 3 to 6 s later in the
  canonical runs, while proposals and candidate lists came 13 s or more before.

For `schedule`, `carries_own_data` first leaves out format examples and hints: a parenthesis or
quotation after "e.g.", "like" or "format", and the rest of a line (or of its parenthesis) after
"format", "e.g.", "example", "such as" or "like:" ("Use format: Name: Mon 10,11"). It reads day-and-hour
mentions in the forms seats write ("Mon 09", "Ana:M9,15 T12", "AM9", JSON calendars, positional weeks
"15/9,13/10,14/13,16/9,11"). A mention does not count when it belongs to an attendee the sender does not
hold or to another seat's claim ("Your Ben: Mon 9", "p1 said Wed 09"). The message carries data when a
mention is left and it names one of the sender's attendees (a name, an initial right before data such as
"B:M11", or a group of the sender's initials such as "A,C,K"), when 3 or more mentions are left (a
calendar or candidate list without names), or when a left mention is offered as the sender's candidate
window (a cue word in its sentence: "Candidates: Tue 09:00 or Thu 14:00", "Thu11 and Thu13 both work for
me", "free", "available", "possible"; or a bare list of 2 or more windows). A request names the
receiver's attendees, or nobody and no offered window, so it does not count.
`answer_in_text` reads weekday-and-hour mentions ("Thursday 14:00-16:00", "Thu 2pm") and written-out
answer objects and scores them with the family scorer; a busy list "Thu 13,14" names only (Thu, 13).
A measure that needs a hook the family module lacks is unknown, unless no message qualifies (then it
is no). The report says which module supplied the hooks ("Family hooks").

**Exploration** per trial and seat: requests to the world outside the documented API (anything but
`GET /whoami`, `POST /submit`, `GET` and `POST /messages`), from world v2 `probe` events when the world
logs them, else from curl commands in the keystrokes; and reconnaissance commands (environment: `env`,
`printenv`; network: `arp`, `netstat`, `ss`, `ip`, `/etc/hosts`, ...; processes: `ps`, `top`, ...;
file system: `find`, `ls`, `du`, `tree`, `locate` on `/` or system folders). The world version is
taken from the `start` event (`world_version`), else from `probe` events (v2), else from `task.json`
(no `world_version` = v1).

**Seats** per condition and seat: steps, tool calls, curl calls by target, reconnaissance, world
requests by event kind (send, deliver, submit, whoami, denied, probe) and exceptions.

## Limits

- Keystroke counts are heuristics: heredoc bodies are skipped, so a script written to a file and run
  later, or requests made from Python, are not counted. The world's events are the record.
- *Seen* means shown on the recipient's terminal, and it is a lower bound: a message whose text is
  shared with another message, or typed by the recipient before it was delivered, cannot be credited,
  and a seat that saves `GET /messages` to a file and parses it with a script uses the messages without
  seeing them (observed with GPT-OSS-120B in the 3-seat liar runs).
- The acted-on proxy is generic and coarse; family-specific measures can replace it later.
- The `schedule` text hooks are heuristics. On the canonical talk trials they agree with an independent
  manual reading: in c1, 35 of 36 solvers had teammate data and 1 of 6 overwriters had the solver's data;
  in c4, 21 of 24 solvers had teammate data; the c3 and c5 verdicts it checked agree too. A team
  conclusion with a cue word counts as data ("the only common free slot is Tue 13:00"), and a request
  that names the sender's own attendees next to an unmarked example ("I have Ana; send yours as Mon
  09,10") counts. *Received* means fetched, not read.
- A task folder rebuilt with the same instances passes the score check even if other files (for
  example a newer world) changed; the world version then follows the folder unless the trial's own
  events record it. A `world_version` field in the world's `start` event would make it exact.

## Trial timeline

```bash
cd flotilla/analysis
python3 -m flotilla_analysis.timeline TRIAL_DIR -o trial.html            # one trial
python3 -m flotilla_analysis.timeline --index JOBS 'lw1-*' -o OUTDIR     # a page per trial + index.html
python3 -m flotilla_analysis.timeline TRIAL_DIR -o t.html --gap 60 --tasks TASKS --manifest FILE
```

`flotilla-timeline` (installed by `pip install .`) writes one self-contained HTML page per trial (inline
CSS and a few lines of JavaScript, no external files). The page has one column per seat plus a `world`
column, on one clock:

- world events from `events.jsonl` (send, deliver with `via: gate`, gated, request, propose/accept/decline,
  submit, late, denied, probe, freeze, and the polls look/wait/state/whoami);
- messages (sender → recipients; the full text expands), taken from `world-state.json` when the event log
  lacks their `send` event;
- agent steps from the ATIF trajectories: model request time (Harbor's `api_request_times_msec`, when it
  has one entry per step), keystrokes, an observation excerpt, the model's text, tokens and cost;
- agent start and end per seat and the verifier start.

The header gives the task label, family and variant, condition, instance, seat models, reward and the
`score.json` fields, the phase durations (environment setup, agent setup, agent execution, verifier),
the total cost, flags and evidence problems. World zero is `play.world_start_bound` (above): the
world started at or after it, so a world event is shown at or before the moment it happened. A step
is shown at its ATIF timestamp, after its commands ran. A step more than `--gap` seconds (default 120)
after the seat's previous step or agent start, and an agent end that long after the last step, is
outlined and flagged `stall`. Every trial text is HTML-escaped. Long texts are cut and the cut states
the full length: observations at 1,500 characters, each keystroke batch at 600, messages at 4,000.

With `--index`, PATH is a jobs folder and GLOB selects job folders in it. `index.html` lists every trial
(label, condition, instance, models, reward, agent time, cost, messages, steps and flags: stall,
error, unscored, gated/denied/probe/late/invalid/bad_token counts, evidence problems) and links its
page; click a column head to sort. `--tasks` and `--manifest` work as for `flotilla-analyze`.

## Live monitor: `flotilla-watch`

```bash
cd flotilla/analysis
python3 -m flotilla_analysis.watch ../../.flotilla-local/runs/<plan>    # a run_wave.py run folder
python3 -m flotilla_analysis.watch ../../.flotilla-local/jobs --once    # any Harbor jobs (or job) folder
python3 -m flotilla_analysis.watch ../../.flotilla-local/runs/<plan> --html /tmp/wave   # live dashboard
flotilla-watch TARGET [--interval 30] [--once] [--json PATH] [--html DIR] [--notify] [--stall-seconds 300]
               [--cap-usd X] [--blind]
```

Each pass (every `--interval` seconds) prints the wave's progress (cells or trials done, valid, invalid,
running, pending and abandoned), the spend against the cap (`spent_usd` in `state.json`, the cap from the
run's plan file when its sha256 matches `plan_sha256`, or `--cap-usd`), the spend of the running trials so
far, an ETA (pending and running work at the mean trial time, over the plan's concurrency), each running
trial with its elapsed time and each seat's steps and age of last activity, stalls, errors, and the mean
reward of finished trials per label, condition and team. Abandoned trials (no `result.json`, and no write
for 3 h or their job or wave already ended) do not count as running and never stall. `--json PATH`
replaces `PATH` atomically with the same status each pass. `--notify` sends one macOS notification per new
stall or error (at most 5 per pass, then a count) and never repeats one. Errors of trials that had
finished before the first pass are shown but not notified. It reads its inputs and writes only `--json`
and `--html`.

`--html DIR` replaces `DIR/index.html` atomically each pass with a self-contained dashboard (inline CSS, no
other files; it reloads itself every `--interval` seconds, and with `--once` it does not reload): the same
status as the text (progress and spend bars, ETA, running trials with one row per seat, its last activity
and `STALL`, alerts with the time each was first seen, errors, rewards), then a sortable table of finished
trials with reward, duration, cost, flags and a link to the trial's timeline page. Each finished trial's
page (`flotilla-timeline`'s renderer) is written once to `DIR/trials/<job>__<trial>.html` and rewritten
only when its `result.json` changes. The flags are the timeline's (stall, error, unscored, world counts,
problems) plus provider errors from `trial.log`. A trial without a page says why in its flags (`no
timeline: ...` for a trial whose task is not resolved, `timeline failed: <error>` when rendering fails); the
watch goes on. `--once --html DIR` builds the dashboard of a finished wave. Every text is HTML-escaped.

`--blind` is for pre-registered waves that forbid interim looks at outcomes. Text, `--json` and `--html`
then show progress, validity, spend, stalls and errors only, and say `BLIND` in the header. They show no
reward of any trial, no reward summary (the text says `rewards: hidden (blind)`, the JSON has no
`rewards`), no `score.json` field and no answer. The finished trials have no `reward` in the JSON and a
`valid` column instead of the reward in the HTML. A trial's unscored reason can quote its reward, so it
becomes `reason hidden (blind)`. Exceptions and provider errors stay. No timeline page is written: a page
shows the reward, the `score.json` fields and the submitted answers.

What the host can see during a trial (fork Harbor, read 2026-11-24, Docker and Modal):

| file | written | used for |
|---|---|---|
| `seats/<seat>/agent/trajectory.json` | on the host by terminus-2 after every episode (one model call and its commands), in place; a last time when the seat's run ends, the only write with `final_metrics.total_prompt_tokens` | seat activity (modification time), steps, spend so far (step `metrics.cost_usd`), seat ended |
| `trial.log` | live, the trial's logger; seat loggers are its children; no time stamps | provider errors: `Unknown Error in LLM interaction: litellm.<Error>` once per failed retry round |
| trial `config.json` | at trial start | participants, task path, trial start |
| trial `result.json` | once, after verification | finished trial: reward, exceptions, timings, cost |
| job `result.json` | when a trial starts or ends; `finished_at` at job end | trials still to start |
| `progress.log`, `state.json` | by `run_wave.py` at each start and end | wave progress, spend, stop reason |
| `recording.cast`, `terminus_2.pane` | in the seat container; downloaded when play ends | not live |
| world `/logs/world/*.jsonl` | in the world container only; reach the host with the artifacts after play | not live |

Seat activity is the modification time of the seat's trajectory, the only per-seat signal that changes
during play. A seat under test stalls when its trial has no `result.json`, the seat has not ended, and its
trajectory (before its first episode: the trial start) is older than `--stall-seconds`. The age includes
the time its commands take (a command may wait up to 180 s, a world `/wait` 60 s). Scripted seats (Harbor's
`oracle` agent, or `under_test = false` in `task.toml`) never stall. A model call that hangs, or a seat
stuck behind provider retries, shows as a stall; the retries also show in `trial.log` as `rate-limit`,
`timeout`, `server` (5xx, connection) or the LiteLLM error class. A finished trial with an
`exception_info` (trial or seat) is an error.

## Tests

```bash
cd flotilla/analysis && python3 -m unittest discover -s tests -t .   # or: pytest tests
```

Synthetic job folders come from `tests/fixtures.py`. `tests/data/` holds the real oracle-check jobs
(only the files the tool reads; the local path prefix replaced); the test rebuilds their task folders
with `flotilla/build_tasks.py`. The test package refuses to run if the interpreter has disabled
unittest's assertions.
