# Can a teammate’s record change the physical outcome?

A warehouse loader knows which two exhibits to dispatch. Only its teammates hold the two links from exhibit to crate to bay. This example asks whether a different peer filing can make a **different correct physical decision** possible. It does not score the quality of a written report.

## Use it now

```sh
python3.12 -BS -m examples.seatlab.physical_dispatch certify --start 7
```

The command needs only Python 3.12 and the fork checkout. It runs a deterministic native development check, not an agent evaluation. It searches at most 128 natural lottery draws. It accepts the first seed for which the realized order equals the lexicographically first public brief. The role policies do not see the draw before they act. No lottery result is changed. The output gives the selected seed and the conditional result.

For development seed 10, both filing policies send 3,160 bytes to the final loader. With unrelated cards, the best common dispatch succeeds in 1 of the 2 equally probable warehouse maps. With the relevant cards, one rule that follows the two visible chains succeeds in both. These are exact finite optima, not model success rates. The focal order has weight 3 of 10 on this seed.

## What changes

Two equally weighted warehouses share the initial request, public labels, lottery, registrar history and legal actions. One warehouse swaps the final destinations of the focal crates. Only their private depot history records that change. The registrar files the 2 current cards for the focal order. The depot then files exactly 2 current cards:

- **Other crates:** file the first 2 crate keys outside the received agenda. The loader receives the same complete owner request in both warehouses. It needs different final routes.
- **Relevant crates:** file both agenda keys. The loader can follow both record chains, without knowing the hidden warehouse or the answer key.

Each warehouse permits 64 final dispatches: 8 bays for each of 2 exhibits. Sending both to the same bay is legal but incorrect. Enumeration uses the physical verifier. A lucky unsupported dispatch can win; a documented but incorrect one loses. The [finite decision tool](../decision_boundary/README.md) checks that the score of every dispatch stays fixed within each warehouse across the two filings. It groups the actual final loader requests by literal equality, including the first action in the loader’s history.

The optimum is conditional on these 2 maps, this natural focal draw and the fixed upstream policies. The result is not the reward of an unrestricted team, an estimate over all orders, or a claim about frontier models. The swap defines an explicit variant; do not call it a second stock seed.

## Prepare an actual native task without running it

```sh
umask 077
work=$(mktemp -d /tmp/exhibit-physical.XXXXXX)
python3.12 -BS -m examples.seatlab.physical_dispatch materialize "$work/task"
python3.12 -BS -m examples.seatlab.physical_dispatch check-task "$work/task"
```

Use a new path outside the checkout. Materialization copies the pinned stock task and adds the private map setting to the main world only. The original task and its locks remain unchanged. Both maps use the same staged task, role instructions, seat images and physical verifier. The roster has 3 real native roles and 4 turns.

For native input preparation, use the fork’s Python environment. Read the development seed from the certificate; do not substitute a public development case for a held-out case.

```sh
seed=$(python3.12 -BS -m examples.seatlab.physical_dispatch certify --start 7 | python3 -c 'import json,sys; print(json.load(sys.stdin)["development_seed"])')
python -B -m examples.seatlab.physical_dispatch prepare "$work/task" "$work/inputs" --seed "$seed"
```

This produces reference configs and native locks for `original` and `swapped`. It does not start a team, run an oracle, buy compute or establish model difficulty. Stock Exhibit Relay enrollments bind to a different task and cannot admit this variant.

## Prepare a controlled final receiver

`prepare` above checks two maps with the stock reference roster. It does **not** assign the experimental filing policies. Use `prepare-receivers` to bind the actual four conditions. It fixes nomination and both filings from each seat’s own legal view. Only the final loader decision goes to an external owner. This estimates receiver use, not spontaneous team coordination.

Supply four different waiting `seatlab.seat-turn/v1` Unix sockets. Use opaque names in a private directory. Each socket must belong to your user and have mode `0600`. The existing [one-turn owner adapter](../visibility/local_seat.py) documents the wire interface. A socket identifies a local endpoint, not a model or an independent context.

```sh
python -B -m examples.seatlab.physical_dispatch prepare-receivers "$work/task" "$work/receiver-inputs" --seed "$seed" \
  --original-other "$owners/r1" --original-relevant "$owners/r2" \
  --swapped-other "$owners/r3" --swapped-relevant "$owners/r4"
```

This command does not connect or start a trial. It refuses a nonfocal draw rather than selecting a replacement. It writes four private native configs, their locks, and a receipt. It records private case commitments, role instructions and a host source fingerprint. It checks every config and lock field. The fingerprint identifies the checkout’s harness files, not an external owner or provider. Only the trial label, hidden map, depot rule and opaque loader socket may differ. The seat receives no condition label, map, seed, or answer key. Its input contains the records its teammates filed, which can reveal the map. Keep the receipt and config files outside the participant context.

## Enroll and audit a study without buying a run

Use one private roster for the full study. `enroll` commits to 4–64 eligible warehouse cases, in blocks of 4. It uses the stock lottery. It does not edit a draw, consult model output, or select cases by reward. Within each block, every condition occupies every position once. Every ordered pair of different conditions occurs once as neighbors. Each block receives an independent schedule draw; draws can repeat. It does not prove the schedule was followed.

These commands demonstrate the workflow with a **public development seed**, not a held-out result. Run them from the fork root, using the private `$work/task` created above:

```sh
mkdir -m 700 "$work/cases"
python3.12 -BS -m examples.seatlab.physical_dispatch enroll "$work/study" --cases 4 --development-seed 7 > "$work/registration.json"
pin=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["cohort_digest"])' "$work/registration.json")
python3.12 -BS -m examples.seatlab.physical_dispatch check-cohort "$work/study" --expect "$pin"
python3.12 -BS -m examples.seatlab.physical_dispatch qualify-case "$work/study" case-0001 --expect "$pin"
python3.12 -BS -m examples.seatlab.physical_dispatch score-cohort "$work/study" "$work/cases" --expect "$pin"
```

The empty study has 16 reserved decisions, no observations, no point estimate, and bounds of `[-1, 1]`. `qualify-case` enumerates the full native payoff matrix for the exact selected case. It refuses to substitute a different seed. A selection or qualification failure remains visible; do not silently enroll a replacement.

For a future private study, omit `--development-seed`. Keep the printed commitment outside the editable study directory **before** revealing any decisions. Keep the private design, seeds, schedule and verifier artifacts away from every participant. A private draw is not proof that an operator never selected among several rosters. Fix the model version, budget, trial policy and analysis before collecting outcomes.

Prepare each case in its reserved directory. Use four fresh one-use sockets; do not reuse a conversation across cells or cases. The following command writes inputs without contacting any owner:

```sh
python -B -m examples.seatlab.physical_dispatch bind-case "$work/study" case-0001 "$work/task" "$work/cases/case-0001" --expect "$pin" \
  --original-other "$owners/r1" --original-relevant "$owners/r2" \
  --swapped-other "$owners/r3" --swapped-relevant "$owners/r4"
```

The binding includes the exact trial config, role instructions, locks and the case certificate. Only the verifier receives `EXHIBIT_RETAIN_CUT=1`. The cut contains private world state. Preserve the staged task at its prepared absolute path and keep the matching fork revision. Earlier plans without this cut cannot support a retrospective claim about what the receiver saw.

After an independently authorized run, use the fork's Python environment to read the original native records:

```sh
python -B -m examples.seatlab.physical_dispatch inspect-trial "$work/study" case-0001 "$work/cases/case-0001" original-other "$archived_trial" --expect "$pin"
python -B -m examples.seatlab.physical_dispatch score-cohort "$work/study" "$work/cases" --expect "$pin"
```

`inspect-trial` matches the original actions and byte-level owner requests from all accepted turns. It uses the native result, verifier cut, observation and reward. A wrong completed dispatch scores 0. A frozen incomplete trial has no reward. Missing or inconsistent evidence also has no reward, with separate status codes. An accepted final request is not evidence of an unaccepted attempt or the identity of a model. A moved copy of one trial can be inspected; cohort scoring uses only its reserved native `trials/<trial_name>` slot.

`score-cohort` reports the result for every reserved case, not just successful trials. It averages **relevant minus other** within each map, then averages the 2 maps within each case. It reports per-condition missingness, exact lower and upper bounds, and a separate mean for complete cases. These are descriptive bounds on the enrolled sample, not confidence intervals or estimates for all possible warehouses. All cells that reuse a native trial ID become unknown. The report compares recorded times with the planned order and flags overlap. An order conflict does not erase a valid physical grade. Do not use a conflicted case as evidence of order compliance. An operator can replace local files. Preserve independent provider and first-attempt records for any empirical claim.

## Future experiment, not run

First choose the estimand. For a receiver-only study, fix the three upstream actions using the view-only policies. Use each combination of the 2 maps and 2 depot filing policies once per case. Follow the precommitted order with a fresh loader context for each condition. Do not change the order of the 4 actions inside a trial. Compare exact physical reward within each independent case. Do not disclose the seed, map, filing condition, other arm, or expected answer to a participant. A prior context shared across arms invalidates the comparison.

For a live team study, use separate held-out cases and let every role make its own decisions. Report the whole-team native reward. Do not label its difference as the effect of a fixed handoff. Record all selected cases, the eligibility rule, failures and missingness independently of reward. A case with no final dispatch has no measured native reward.
