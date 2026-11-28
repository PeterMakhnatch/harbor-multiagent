# Test a new partner with ordinary Harbor participants

Use this when you want **fresh native trials**, not recombinations of recorded votes. Select 2–5 original teams. The study registers every ordered east/west pairing on the same 6 Joint Reopen calendars. The 2-team design has 24 trials. The 5-team design has 150. Each native trial starts both seats. Registering, emitting configs and inspecting results do not launch any of them.

The comparison asks: **On the same cases, how often do the original and mixed teams both vote for the feasible slot?** Both original bookings are invalid. The native world delivers solicited reports. Nothing forces ordinary model seats to read, report truthfully, follow the supplied conventions or agree. A native reward is about the team, not about which role deserves credit.

## Prepare without starting trials

Use an existing development installation of this fork to load the native `Participant` and `TrialConfig` types. Run from the repository root. These commands need no API key or Docker daemon. Keep the workspace private. Its registration contains the case seeds.

This study pins the native task package that includes both private role instruction files. Older Joint Reopen registrations omit these files from their package digest. Their checksums can match, but their package digests do not. Do not mix their trial folders or relabel their results.

```sh
umask 077
work=$(mktemp -d)
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
command_py=python3
partners() { "$command_py" -B -m examples.seatlab.partner_native "$@"; }
partners register --control different --start 9327 --out "$work/study" > "$work/issued.json"
pin=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["study_sha256"])' < "$work/issued.json")
partners audit --study "$work/study" --pin "$pin"
reg="$work/study/registration.json"
```

`different` makes one original team use the forward convention and the other the reverse convention. The owners see only their role view, their own convention and the 3 public alternative labels. Those labels identify the alternatives used by the cohort, not the correct slot for the current trial. Both original teams should score 6/6. The 2 mixed pairs should each score 3/6. As a separate negative control, register `--control same` to a different output directory. All 4 pairings should score 6/6. These are **predictions** for the scripted native study, not measured model results.

Run the bundled owners against the native world and verifier without starting Harbor or Docker:

```sh
partners rehearse --study "$work/study" --pin "$pin"
```

The rehearsal accepts only bundled scripted controls. It does not run a program from a supplied participant roster or write trial results. The native collector will still report all registered trials as missing after a rehearsal.

For actual agents, replace `--control different` with `--team alpha=/absolute/alpha.json --team beta=/absolute/beta.json`. Each file is an existing [Joint Reopen participant roster](../../joint_reopen/README.md#register-a-private-study-for-your-own-harness). Both roles remain separate participants. An east participant keeps exactly the same config when the west partner changes, and vice versa. Names, model identifiers and file hashes do not prove independent implementations or which model served a request. Stock participants receive the ordinary task instructions. They do **not** automatically receive the 3 public labels or the one-slot restriction from the scripted design. Use a common, documented role adapter when equal information is part of the claim.

## Give operators disjoint trials

For a scripted qualification, first select the included executable for both roles. Do not set these paths if you want to test a different owner program.

```sh
export JOINT_REOPEN_EAST_OWNER="$PWD/examples/seatlab/partner_native/owner.py"
export JOINT_REOPEN_WEST_OWNER="$JOINT_REOPEN_EAST_OWNER"
"$command_py" -B -m examples.native_owner_preflight "$reg"
for index in 0 1; do
  "$command_py" -B -m examples.joint_reopen.study emit "$reg" --shard "$index/2" --out "$work/lane-$index"
done
python3 -BS -m examples.native_worklist status \
  --study "$reg" "$work/lane-0" 0/2 --study "$reg" "$work/lane-1" 1/2
```

The native owner preflight applies to external owners. If all participants are stock agents, its “no supported external seats” result is not a readiness test for those agents. `status` does not reserve work. Later, when runs and their cost are approved, each operator uses the stock `native_worklist take` on their own shard. Start the returned config with the fork's ordinary `harbor trial start -c`. Do not run the same config twice or issue replacement tickets in an existing cohort. Limit actual Docker concurrency separately. A worklist reservation is not proof that Harbor started a trial.

## Read results without losing missing teams

```sh
mkdir -p "$work/lane-0/trials" "$work/lane-1/trials"
partners score --study "$work/study" --pin "$pin" \
  --trials "$work/lane-0/trials" --trials "$work/lane-1/trials"
```

`score` returns `0` for complete registered evidence, `1` for incomplete evidence with a panel, and `2` for invalid input without a panel. It uses the existing native collector. It does not infer a zero from a missing trial, worker reservation, verifier error or ambiguous directory. With no results, each pairing has reward `null` and bounds `[0,1]`. The original-minus-mixed difference lies in `[-1,1]`. These are bounds from missing evidence, **not confidence intervals**. A complete result for one balanced cohort describes that cohort. It does not estimate independent model-to-model variation. For a new prospective cohort, choose a fresh start and a new registration.

## See what a team average hides

`partners explain --study "$work/study" --pin "$pin" --trials "$work/lane-0/trials" --trials "$work/lane-1/trials"` reads the same native results as `score`. The exit codes are the same. It answers two questions:

1. **Which outcomes change when one role keeps its configuration but gets a new partner?** Each comparison matches the same 6 calendars. `original_only` counts wins that disappear. `replacement_only` counts wins that appear. Bounds give the range still possible when a trial is absent. A mixed team appears in more than one comparison. Do not add those comparisons.
2. **What happened before the votes?** The matrix separates a missing vote, different votes, agreement on an unavailable slot, and a repaired booking. It also counts when the world returned an inbox before each role voted. An empty report still counts as a return. This is not proof the role used the message. An absent trial has no observed events.

Try `--control offsetting` in a **new** study directory. Register and rehearse it as above. Both east roles decode the same convention. Both west roles send the same report and request their inbox. One votes for its earlier available slot; the other votes for its later slot. The prediction is 3/6 for every team. Keeping east and changing west should reverse all 6 outcomes. Keeping west and changing east should change none. The rehearsal checks the matched outcomes as well as the averages. Its `diagnosis.source` is `scripted_native_worlds`. Only `explain` on collected trials reports `collected_native_trials`. Neither output contains raw reports, cards, case seeds or ticket IDs.

For the [controlled two-stage benchmark](../partner_switch/README.md), the vote stage sees exactly the reports emitted by the selected partners. Each identical private observation gets one stored reply. The native experiment here permits fresh, stateful behavior on every trial instead. Scores from the two designs are not interchangeable. Neither command authenticates model identity.
