# Measure whether a team reopens the right decision

Run [Joint Reopen](../tasks/joint-reopen/README.md) when you need a known decision boundary inside a multi-agent task. First the two seats endorse one provisional booking. Then the world releases new private evidence. A team must **keep the booking when it still works** and **revise it when either seat can no longer attend**. The two requirements prevent an always-keep or always-switch policy from looking good by itself.

The task comes from a [failure in archived model teams](../seat_handoffs/README.md#is-first-agreement-a-safe-time-to-stop). That evidence motivates the task. It is not a model result on this new benchmark.

## Run one native team

Work at the root of an installed copy of this Harbor fork, with local Docker available. Use a fresh trial directory. The example uses two separate native `oracle` participants, not LLMs.

```bash
uv run harbor trial start -c examples/joint_reopen/sample-trial.yaml
```

To test regular agents, copy the config. Set each participant's native `name` and `model_name`, keep distinct `seat` values, and remove `JR_POLICY`. Configure credentials through the approved provider adapter outside the task. The world and the verifier need no provider credentials. Record the exact task checksum, roster, exposure limit, and first attempts before a model study. A configured model name is not proof of which model served a turn.

## Bring your own decision loop

Use this when you want each participant, rather than the benchmark, to decide whether to inspect its card or ask for the other report. A native [roster](samples/owner-team.json) maps each role to `JointOwnerSeat`. Run one complete team with the supplied scripted programs:

```bash
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
export JOINT_REOPEN_EAST_OWNER="$PWD/examples/joint_reopen/scripted_owner.py"
export JOINT_REOPEN_WEST_OWNER="$PWD/examples/joint_reopen/scripted_owner.py"
space=$(mktemp -d)
reg=examples/joint_reopen/samples/owner-registration.json
uv run python -m examples.joint_reopen.study emit "$reg" --out "$space/east-west" --shard 0/12
cfg=$(python3 -BS -m examples.native_worklist take --study "$reg" "$space/east-west" 0/12)
trial="$space/east-west/trials/$(basename "$cfg" .json)"
uv run harbor trial start -c "$cfg"
```

To use another harness, point each variable at an absolute executable path. The program receives one JSON object on stdin per decision. It returns one JSON object on stdout: `{"schema":"harbor.joint-reopen.choice/v1","requested":"…","choice":"…","served_model":null}`. Echo the request's `requested` identifier and select an entry from `view.choices`. Use `null` for `served_model` unless the program can name the model. The identifier is self-reported.

The view contains the role, provisional slot, public options, turn, stage, and what this seat actually requested. `card` and `inbox` start as `null`. An inbox returned empty is different from an inbox withheld. A truthful report, an empty report, another legal report, an optional read, a final vote, and a deliberate stop are all real choices. No strategic action happens automatically. A missing executable is a seat error, not a vote or a stop.

The host executable is trusted. It receives no seat token, provider key, peer path or study setting through this interface. A shared host user, filesystem, session store or network can still connect the programs outside the world. Isolate these when measuring communication. A new process starts each turn; the view carries the seat's task state. The command does not start a paid model by itself.

### Inspect an unfinished team

Run this command from the same checkout, in another terminal, while Harbor runs:

```bash
python3 -BS -m examples.joint_reopen.progress "$trial" --json
```

Pass the native trial directory, not its `trials` parent. The reader shows the last marker from each instrumented seat. `owner_turn` means the adapter requested a decision. `request` names an action it is about to attempt. `barrier` means the world had not yet reached the named phase on a status check. `confirmed` lists actions the world accepted. It includes no card, report, vote contents or seed.

A marker can outlive its process. It does not establish that the process is alive, why the partner is slow, or whether the team is deadlocked. The two markers are separate snapshots, not a simultaneous world event. Stock participants do not produce them. `not_observed` is not inactivity. A present `result.json` is not a verified reward.

After the run, use `collect` below. Its optional `owner_evidence.roles[ROLE]` compares the declared decisions with that seat's ordered world actions. A matched receipt does not authenticate a model or prove it used a report. Evidence can be missing or inconsistent without changing the native grade.

**Qualification history.** The [first registration](samples/owner-registration.json) and its [predictions](samples/owner-predictions.json) precede the harness run. Its first 2 reserved tickets stopped at adapter import, before Harbor created a trial directory. The public instructions exceeded their 1,024-byte limit. The other 10 tickets were not reserved. This qualification failed; neither missing result is a grade.

The [repair registration](samples/owner-repair-registration.json) is a separate qualification. It changes only the ticket IDs. In row order it inherits all 12 predictions, interventions, and pass conditions from the first registration. It keeps the original task checksum, rosters, calendars, report treatment and verifier. The repaired public instructions fit in 755 ASCII bytes. Neither qualification uses a model.

## Register a private study for your own harness

The 32 public controls are for qualification. Do not use their two published seeds to measure model performance. A new registration can cover every private card layout for one public incumbent, without oversampling layouts where both seats guess the same fallback.

```bash
export PYTHONDONTWRITEBYTECODE=1
study=$(mktemp -d)
reg="$study/private.json"
uv run python -m examples.joint_reopen.design register \
  --participants examples/joint_reopen/samples/recheck-team.json --incumbent A --out "$reg"
python3 -BS -m examples.joint_reopen.design audit "$reg" --json
```

The sample uses no model. For regular seats, give `--participants` a public native roster with one `east` and one `west` participant. Each needs its harness `name` and `model_name`. Do not put a credential value in that file. Registration checks the installed native task. It selects 60 private seeds and covers each ordered pair of cards once per fixture and channel. Each of the 240 trials in one arm has the same seed and fixture in the other. Registration does **not** launch these 480 trials. Set a separate spending limit before running any models.

Keep the registration, emitted configs, and workspaces private. A worker that starts Harbor needs these files. A participating agent must not see them. `design audit` reports only aggregate support. It rejects mismatched seed pairs even when the two arms have the same number of each card layout. A valid but unbalanced registration exits 0 with `balanced_paired: false`.

### What can seats do with their own card alone?

Run `python3 -BS -m examples.joint_reopen.information`. It checks all 1,048,576 pairs of vetoed-card rules. Without peer reports, timing, previous trials or the seed, the best expected team reward on the balanced block is **81/240 (27/80)**. Using the same fixed rule in both seats gives at most **80/240**. Sharing truthful cards makes **240/240** possible. These are reference values, not results from live models.

The key constraint is the seat whose original booking still works. Its card alone cannot say whether its partner must change. Over the 3 cases where at most one seat must change, the team can win at most 60 of 180 equally weighted states. When both must change, the best two private vote rules agree on the required slot in 21 of 60 states. An explicit pair of rules reaches both limits. Live Harbor retains phase timing, so the number is not a hard limit for its `withhold` arm. A partial shard cannot be compared to the full balanced block without accounting for the missing trials.

## Distribute a registered study

The [registration](samples/control-registration.json) fixes the task identity, native teams, and all 32 tickets. The [predictions](samples/control-predictions.json) distinguish access to a peer report, its accuracy, the two individual votes, joint feasibility, and team reward. Use the **same manifest bytes and fork revision** for each worker.

```bash
study=$(mktemp -d)
reg=examples/joint_reopen/samples/control-registration.json
uv run python -m examples.joint_reopen.study emit "$reg" --out "$study/worker-0" --shard 0/2
uv run python -m examples.joint_reopen.study emit "$reg" --out "$study/worker-1" --shard 1/2
```

Each ticket runs a whole two-seat team. Give all workers on a host the same workspace for each shard. Do not loop over `configs/*.json`: after an interruption that can restart a ticket and replace its first history. Use `take` to reserve one new first attempt. The command prints a config path; it never starts a model or a container.

```bash
while :; do
  if cfg=$(python3 -BS -m examples.native_worklist take \
      --study "$reg" "$study/worker-0" 0/2 \
      --study "$reg" "$study/worker-1" 1/2); then
    uv run harbor trial start -c "$cfg" || exit 1
  else
    rc=$?
    [ "$rc" -eq 3 ] && break
    exit "$rc"
  fi
done
```

Use a local Docker concurrency limit around `harbor trial start`. Replace `take` with `status` to see available tickets, reservations without a native directory, directories without a result file, and result files. A crash after `take` leaves the reservation in place. Do not remove it to retry. A result file alone does not prove a valid grade. On different machines, assign disjoint shards. Independent copies of a workspace cannot coordinate.

After the workers stop, collect the original first attempts. The cold reader uses only Python's standard library. Omitting a worker must not shrink the denominator.

```bash
python3 -BS -m examples.joint_reopen.collect "$reg" \
  "$study/worker-0/trials" "$study/worker-1/trials" --json
```

Keep `collect` output private: it contains seeds and tickets. Use the public comparison when reporting a run to another team or monitoring an unfinished block:

```bash
python3 -BS -m examples.joint_reopen.effect "$reg" \
  "$study/worker-0/trials" "$study/worker-1/trials" --json
```

`effect` validates the native records. It reports a team index and a fixture, not private seeds, team names, IDs, local paths, or individual traces. It compares reports delivered minus reports withheld, on the same seed, roster, and fixture. A block needs the same positive ticket count in both arms. One-arm controls stay in the registered count but do not estimate a channel effect. Repeated tickets without an individual pair ID contribute to arm averages, not a count of pairs that changed direction.

The interval covers every possible binary value of each unknown grade. It is **not** a confidence interval. A missing receipt does not count as a zero. For example, 1 observed delivered success and no other observations in a 60-pair fixture give a difference between −59/60 and +60/60. These bounds describe missing evidence. They do not justify stopping, and they do not show that a real model used its partner's report.

**Operator qualification.** On one host, 24 concurrent processes competed for the remaining 8 of 12 assigned tickets across Joint Reopen and Shift Board. Exactly 8 returned unique configs; 16 reported no work left. Neither an abandoned reservation nor a pre-existing partial directory was reissued. The two native teams actually launched through that worklist each produced one valid grade. The collectors retained the original registrations of 480 and 4 tickets. This was a scripted operations check, not another model experiment.

## What happened in the 32 registered controls?

All 32 scripted teams finished on the native Docker runner. Two workers ran 16 first attempts each. All 928 predictions matched the verifier. The [result rows](samples/control-outcomes.json) give the ticket, native trial ID, world run ID and observation hash. The two seeds were fixed in advance. These are controls, not model trials.

| Updated calendars, both seats use `recheck` | Rewarded with peer reports | Rewarded without peer reports |
|---|---:|---:|
| Both can keep the original booking | 2/2 | 2/2 |
| Only east must change | 2/2 | 0/2 |
| Only west must change | 2/2 | 0/2 |
| Both must change | 2/2 | 2/2 |

The one-sided cases distinguish sharing from guessing. The seat that could keep the booking had to change its vote to repair the team decision. Without the report, that seat kept the booking and the other seat changed. When both had to change, both picked the same fallback on these two seeds without a report. Success alone is not evidence of communication.

The other controls separate access from use. On one seed, `keep` and `ack-keep` each scored 1/4. Only in `ack-keep` did both seats receive accurate peer reports before voting. An always-switch team scored 3/4. Its two votes also agreed on a feasible slot in the fourth trial, but it gave up a booking that still worked. In the four one-sided trials with just one rechecking seat, all four teams split their votes.

An independent calendar model and verifier replay matched on 10,000 bounded histories, including partially played games and reads after a vote. Those histories are not extra teams. The cold reader checks the native task, roster, setting, grade, and non-repeated IDs. The world publishes no second receipt for its UUID. A hash identifies the observation file, but cannot establish its origin outside the trial directory.

For a model study, keep the seats blind to the condition and seed. Use new, private seeds and repeat both channel arms. A returned report proves availability, not attention. Both arms retain the phase barrier and elapsed time. Withholding report contents does not rule out timing signals.
