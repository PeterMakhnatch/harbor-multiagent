# Exhibit Relay

A small native Harbor task for evaluating the information that passes between agents. The team must dispatch 2 museum exhibits to their correct bays. The world holds the correct routes, but no role initially holds the whole route.

Use this task to distinguish a defective handoff format from a receiver that makes a poor decision despite an adequate handoff. The flagship `coupling` profile shows why even an exact summary of individual demand can lose the joint demand that a downstream role needs.

## A worked example: correct totals, incomplete information

Consider 4 crate labels A, B, C and D at the same epoch. The loader nominates all 4 briefs, and the registrar files the 4 applicable records. The depot can file only 2 cards. The true crate pairs form 2 alternating perfect matchings. Each matching covers every crate exactly once.

| Brief (crate pair) | Layout a weight | Layout b weight |
|---|---:|---:|
| AB | 4 | 1 |
| CD | 4 | 1 |
| AC | 1 | 4 |
| BD | 1 | 4 |
| Total brief weight | 10 | 10 |

In each layout, A, B, C and D each have individual demand weight 5. The depot sees the same private archive in both layouts. The 2 layouts differ only in the weights assigned to pairs, not in the set of pairs.

The relay offers 3 world-generated versions of the handoff:

- **Linked:** the depot receives exact individual demand totals, indexed by crate and epoch. It does not receive the pairs.
- **Masked:** the depot receives the same totals and the true pairs, with every bundle weight set to `0`. Here `0` means *withheld*, not zero demand or a false measurement. The list length, pairs and epochs remain the same as in bundled.
- **Bundled:** the depot receives the same totals and true pairs, with their actual brief weights. The extra information is the joint demand, not more filing capacity.

For a handoff written by a teammate, see [the registrar-authored variant](AUTHORED.md). It preserves wrong and missing claims and separates source accuracy from transport.

With only the flat totals, one response must serve both indistinguishable layouts. Filing AB supports weight 4 in layout a and weight 1 in layout b. Filing CD, AC or BD gives the same combined weight. Filing a different pair cannot improve it. The best common response therefore supports **5/20** of forecast weight across the 2 layouts.

With the weighted bundles, the depot can file AB in layout a and AC in layout b. Each choice supports weight 4, for **8/20** across the layouts. The other heavy pair in each layout is equally good. The masked format preserves the pair structure but does not identify which matching has weight 4.

These fractions measure explicit written evidence, not realized reward. The certificate compares fixed upstream selections, the same depot archive and a 2-card quota. It enumerates legal depot files and checks the bundled reference policy. It does not constrain an arbitrary online team that selects different upstream records or infers missing routes. A randomized common response cannot improve the equally weighted average over these 2 fixed layouts, but a team can still guess correctly.

## Native task

| Role | Private information | World action |
|---|---|---|
| Loader | Forecast briefs, epochs and weights | Nominate briefs; later commit 2 routes for the realized brief |
| Registrar | Exhibit-to-crate history cards | File a limited set of card IDs; author pair claims when enabled |
| Depot | Crate-to-bay history cards | File a limited set of card IDs |

Both maps are bijections at each epoch. History cards use inclusive validity intervals. The loader needs the correct card from each archive to build a complete written route. The weighted lottery selects one brief from *all* forecast briefs, not just the nominations. The world reveals that brief only after both filings. The grader gives reward 1 if both routes are correct, and 0 if either is wrong. Correct routes without supporting cards still count as correct.

The loader acts twice; the other roles act once each. All seat containers and the main world use `network_mode: none`. Each seat has a dedicated, controlled Unix socket for role-local views and legal actions. It does not mount another role's channel or the private verifier control socket. The world validates actions, controls phase order, derives handoffs and freezes the final state. The verifier independently reconstructs the outcome from that freeze. Host owners can supply the role decisions through the SeatLab owner adapter; the world interface does not require a provider connection inside any seat. Keep owner inputs, role views and operator-only evidence separate.

Start with [task.toml](task.toml), the [role instructions](instructions/), [world implementation](environment/exhibit_relay/), and [verifier](tests/verify.py). The [reference policies](solution/policies.py) are scripted controls, not models.

The other profiles are `fork` (3 briefs, 2 nominations and 3 cards per source) and `mesh` (6 briefs, 3 nominations and 4 cards per source). They test scarce record allocation with different overlap patterns. `independent` suppresses the depot agenda while retaining the other handoffs. `silent` removes those handoffs; `open` relaxes capacity. Do not use the latter 2 as matched controls for the bundle format.

## Author workflow

Run the following commands from the repository root in its Python environment. These commands do not launch model seats.

### 1. Qualify the design

```sh
python3.12 -BS -m examples.seatlab.workbench verify exhibit-handoff-twins
python3.12 -BS -m examples.seatlab.exhibit_packet --seed 7 --json
python -m examples.seatlab.workbench exhibit twins --seed 7 --json
python -m examples.seatlab.workbench exhibit design --profile coupling --seed 7 --cases 8 --json
python -m examples.seatlab.workbench exhibit design --profile fork --seed 7 --cases 8 --json
```

`twins` checks that the 2 linked depot views are identical and that the grouped views preserve the true weighted pairs. It reports the attainable common-file weight and the weight from the informed policy. `design` enumerates the explicit evidence frontier for public development cases, not model performance. Run it for a profile before spending money on a team that may lack sufficient information. Do not use public development seeds to enroll a confirmatory study.

`exhibit_packet` uses the same encoder as the external depot owner. With the current role instructions, each world-generated request on development seed 7 is 4,526 bytes. Across matched arms, only 4 weight digits differ. The masked packets are identical across layouts. This check covers the request sent to the owner, not its provider template, token count or internal state.

### 2. Prepare native inputs

```sh
python -m examples.seatlab.exhibit_prepare /tmp/exhibit-author-seed7 --profile coupling --seed 7
```

Use a new output directory outside the checkout. The preparation command constructs native reference configurations, task locks and a matched-input comparison. It binds the task tree and role instructions and checks that the intended world setting differs. Read the returned paths and role-binding status. Preparation is not enrollment or execution. For `coupling`, it compares `masked` and `bundled`. For `fork` and `mesh`, it compares `independent` and `linked`. See [PAIRING.md](PAIRING.md) for the coupling study and [PROTOCOL.md](PROTOCOL.md) for the other profiles.

### 3. Prepare one future depot decision (world-generated handoffs only)

These commands prepare a development roster and validate one native input. They start no owner or trial. Use the fork environment for `exhibit_bind`. The other commands use Python 3.12 and the standard library.

```sh
umask 077
work=$(mktemp -d /tmp/exhibit.XXXXXX)
python3.12 -BS -m examples.seatlab.exhibit_enroll "$work/plan" --pairs 8 --development-seed 7
python -B -m examples.seatlab.exhibit_bind "$work/plan" pair-0001 masked "$work/depot.sock" "$work/inputs"
python3.12 -BS -m examples.seatlab.exhibit_filing "$work/plan" --json
```

`manifest.json` reserves both outcomes for every case. `schedule.json` records the private world seed, layout and randomized arm order. Layout and first arm are balanced. The enrollment also hashes the declared local owner, wire encoder, evidence analysis and native serialization sources. A change to one of these files invalidates admission. It does not pin installed dependencies or a remote provider. A development roster is never a heldout enrollment. Omitting `--development-seed` creates a new private random roster; freeze the protocol and participant setup first. Use distinct worlds for the later whole-team experiment.

`exhibit_bind` uses native admission and creates `config.json`, `lock.json` and `receipt.json`. The loader and registrar use the role-local reference. Only the depot uses the [external owner adapter](../../seatlab/exhibit_owner.py). Before a separately approved trial, supply a fresh, private `seatlab.seat-turn/v1` owner at the configured path. Use the same path, instruction, owner implementation and provider settings in both arms. A provider is not configured by this tool. Do not give the owner the manifest, trial directory, environment or private cut. A Unix socket is a delivery mechanism, not a sandbox or proof of model identity.

After an authorized run, use `python -B -m examples.seatlab.exhibit_capture PLAN TICKET ARM TRIAL_DIR`. Admission checks the native multi-seat config, lock, result, case commitment, accepted action and exact prefix in the retained cut. It recreates the depot request from the native instruction and role history. Its length and hash must match the exact bytes the owner adapter sent for that action. This does not authenticate the external owner or prove how it used the prompt. It copies a small observation, an optional native reward receipt and hashes into the empty study slot. It never copies raw histories or transcripts. The filing report returns only the measured coverage and missingness. It does not report the scripted loader's reward as model performance. No slot is filled by preparing the inputs.

### 4. Inspect a private frozen cut or declared paired verifier reports

```sh
python -m examples.seatlab.workbench exhibit inspect /path/to/private-exhibit-cut.json --json
python -m examples.seatlab.exhibit_pairs /path/to/enrollment.json --json
```

The verifier publishes `observation.json` and, on completion, `reward.json`. It can retain an operator-only frozen cut with `EXHIBIT_RETAIN_CUT=1`. Keep all original job and owner directories private: they can retain role actions, hidden histories and study assignments.

The paired reader accepts a declared roster of matched cases, for example:

```json
{
  "schema": "seatlab.exhibit-pairs/v1",
  "profile": "coupling",
  "arms": ["masked", "bundled"],
  "pairs": [
    {
      "id": "case-001",
      "case_commitment": "sha256:<committed-case-digest>",
      "left": "case-001/masked/verifier",
      "right": "case-001/bundled/verifier"
    }
  ]
}
```

Replace the digest placeholder with the actual committed digest. The verifier folder paths are relative to the manifest directory. The reader analyzes declared artifacts, not model traces or independently authenticated native team receipts. It does not start trials or replace missing cases.

Read the physical reward together with the complete-chain coverage `C` and the per-item coverage `P`. Coverage uses the whole weighted forecast, before the realized brief. Both filings are needed to measure it. `C` is the fraction with both complete chains; `P` is the fraction of weighted individual items with complete chains. A valid incomplete trial has no native grade, but counts as zero for operational success in the paired analysis. Missing or invalid evidence is unknown, not a failed model. Include those cases in the reported bounds.

## What evidence supports a claim?

- The twin certificate establishes a loss of information for a fixed decision problem. It does not establish that models use the new information.
- A reference run establishes behavior of a particular lawful policy and world configuration, not a causal model effect.
- A replay or cut inspection locates a handoff failure in existing evidence. It does not measure a fresh whole-team reward.
- A matched online study tests the intervention with new decisions from every role in each arm. Freeze the generator, roster, seed commitments, budgets and analysis first. The [coupling protocol](PAIRING.md) and the [fork and mesh protocol](PROTOCOL.md) define separate questions and decision rules. Retain independent run identity and actual served-model evidence.

The native score is a binary physical outcome. The forecast weights already determine the lottery, so do not weight the realized reward again. Do not count individual items, alternate layouts or repeated calls as independent team trials. A successful dispatch can result from a guess or from inference using the bijections. A supported dispatch can still fail if the loader uses a record incorrectly. The experiment concerns a particular information channel and budget, not the general benefit of multi-agent systems.

## Design sources

These sources motivate specific parts of the design. Exhibit Relay is not a reproduction of any of their reported model results.

| Source | Design choice used here |
|---|---|
| [Bespoke MAC Bench](https://benchmarks.bespokelabs.ai/multi-agent-coordination-bench/) | Compare matched teams with and without a useful communication channel. Holdout motivates giving teammates different necessary information rather than assuming more messages help. |
| [HiddenBench](https://arxiv.org/abs/2505.11556) | Partition necessary evidence across private roles. Retain an expanded-capacity `open` check to separate evidence access from decision quality. |
| [MASBench](https://arxiv.org/abs/2610.04672) | Use partial observability and deterministic world evaluation. Keep communication capacity explicit when comparing mechanisms. |
| [CoMemBench](https://arxiv.org/abs/2609.32192) | Scope shared information to the consumer, epoch and dependency. Preserve applicable information without delivering unrelated private archives. |
| [Handoff Debt](https://arxiv.org/abs/2606.02875) | Compare bounded structured handoffs and keep the evidence for analyzing a successor's decision, instead of relying on final reward alone. |
| [Project EVE](https://www.anthropic.com/research/project-eve-claude-plays-mmorpgs) | Make cooperation affect a shared world with resource constraints and an externally checked outcome. This task uses a small offline world, not the live game. |
| [SeatLab live confirmation](../../seatlab/live-confirmation.md) | Distinguish a change in information use from a change in native team reward. Naming changed reports improved allocation of limited reads but did not establish a reward gain. A separate receiver replay was not a new team run. |

The practical author question is: can the next role recover what it needs under the actual budget? Establish that first, then measure whether fresh teams use it.
