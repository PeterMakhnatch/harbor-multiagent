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

## Outputs and rules

Missing is not zero: a value a file does not record is `n/a` (JSON `null`) and is left out of means.

**Scores** per label, team and condition: instances, scored trials, unscored trials (not finished, no
reward, reward outside [0, 1], or `reward_valid` false) with reasons, and the mean of per-instance
mean rewards with a 95% Student t interval over instances (df = instances − 1), clipped to [0, 1].
An interval of zero width (every instance equal) is flagged: it understates the uncertainty.
The same mean is also given without trials flagged `example_leak` (below).

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
with a 10,000-resample percentile bootstrap over instances. Recovery rule: undefined when the mean gap
is ≤ 0; a resample whose gap is ≤ 0 is undefined; if more than 2.5% of resamples are undefined the
interval is reported as undefined (the data cannot exclude "no gap", as with Fieller's unbounded set);
otherwise the 2.5th and 97.5th percentiles of the defined resamples (Hyndman–Fan type 7), with the
number left out. Draws come from `random.Random("<seed>/<label>/<team>/<purpose>")`.

**Cost** per condition: input tokens (cache included, Harbor's convention), cached and output tokens
and USD per trial, summed over the seats under test (a scripted seat is not part of the team). A trial
with any seat's value missing is incomplete and left out of the means. Ratios talk/solo and
silent/solo are ratios of mean per-instance values over instances complete in both conditions.

**Message funnel** per trial and condition, over (message, recipient) pairs. Recipients are the
other seats under test for a message to `all`, else the named seat. *Delivered*: the message id is in
`data.ids` of a `deliver` event of the recipient. *Seen*: delivered, and a distinctive probe of the
message appears in the observation of a recipient step that did not end before the delivery could
have happened:

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
`unknown` without a usable task folder. In `silent` the same count measures lucky answers: it is the
baseline for `talk`. For `schedule`, `oracle_answer` takes the first window that fits, so a briefing
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

**Found and lost answers** per talk trial (conditions `talk*`), and counted per label, team and
condition (won = found and final answer right):

- *found*: some submission scores full with the family scorer; *final_right*: the recorded final score is
  full; *lost*: found and the final answer wrong. The *solver* is the seat of the first right submission
  (world submission order).
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
"B:M11", or a group of the sender's initials such as "A,C,K"), or when 3 or more mentions are left (a
calendar or candidate list without names). A request names the receiver's attendees or nobody, so it
does not count.
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
  in c4, 18 of 24 solvers had teammate data (the reading found at least 17); the c3 verdicts it checked
  agree too. They do not count 1 or 2 candidate windows without a name ("Thu11 and Thu13 work for me"),
  and they count a request that names the sender's own attendees next to an unmarked example ("I have
  Ana; send yours as Mon 09,10"). *Received* means fetched, not read.
- A task folder rebuilt with the same instances passes the score check even if other files (for
  example a newer world) changed; the world version then follows the folder unless the trial's own
  events record it. A `world_version` field in the world's `start` event would make it exact.

## Tests

```bash
cd flotilla/analysis && python3 -m unittest discover -s tests -t .   # or: pytest tests
```

Synthetic job folders come from `tests/fixtures.py`. `tests/data/` holds the real oracle-check jobs
(only the files the tool reads; the local path prefix replaced); the test rebuilds their task folders
with `flotilla/build_tasks.py`. The test package refuses to run if the interpreter has disabled
unittest's assertions.
