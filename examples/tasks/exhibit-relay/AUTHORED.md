# Exhibit Relay: a handoff written by a teammate

Can a registrar preserve which exhibits travel together and which pairs matter most? Can its depot use that information under a 2-card limit? This variant lets a team member choose the evidence. It never supplies a missing claim or corrects a false one. The [world-generated controls](PAIRING.md) answer a different question.

**Status: ready for offline qualification. No model seats have run on this task.** Use a fresh checkout of `seatlab/registrar-authored-handoff` on [Peter’s fork](https://github.com/PeterMakhnatch/harbor-multiagent/tree/seatlab/registrar-authored-handoff).

```sh
python3.12 -BS -m examples.seatlab.workbench verify exhibit-authored-channel
```

This command checks the exact archived certificate for development seed 7. It uses 2 layouts and 6 legal messages per layout: faithful, omitted pair, wrong weight, empty, heavy pairs only, and explicit unknown. It checks 24 delivered role views. They are not trials or independent samples. An intact full message and its masked control have equal size: 4,558 bytes in the complete external owner request. Only 4 weight digits differ. A sender that selects only the heavy pairs can make a correct decision possible even after all weights disappear.

To **prepare but not run** native team inputs, use the fork’s Python environment:

```sh
umask 077
work=$(mktemp -d /tmp/exhibit.XXXXXX)
python -B -m examples.seatlab.exhibit_author_prepare "$work/inputs" \
  --seed 7 --layout a --loader-request "$work/request.sock" \
  --loader-dispatch "$work/dispatch.sock" --registrar "$work/registrar.sock" --depot "$work/depot.sock"
```

This writes 2 native configs, 2 locks and a receipt. It checks the operator’s inputs, not a served model, communication event, or treatment effect. Both configs name the same 4 future turn endpoints. Use fresh, isolated owners for every role and arm; never run both against the same live owners. Never give an owner the config directory or operator cut. Keep the retained evidence private.

## Inspect a saved filing without a receiver call

For a first use, create a **scripted development fixture**. This does not run a model or an evaluation. The registrar writes two correct heavy pairs but files source cards for just one. The fixture makes the depot select the other pair. No loader makes a final dispatch.

```sh
umask 077
work=$(mktemp -d /tmp/exhibit.XXXXXX)
python3.12 -BS -m examples.seatlab.exhibit_example "$work/development-cut.json"
python3.12 -BS -m examples.seatlab.exhibit_saved "$work/development-cut.json" "$work/requests"
python3.12 -BS -m examples.seatlab.exhibit_options "$work/development-cut.json" --json
```

For a later authorized team, use the verifier’s `private-exhibit-cut.json` instead of the example. Enable `EXHIBIT_RETAIN_CUT=1` before collection. The reader requires a regular file you own, mode `0600`, at most 128 KiB. Keep the original native job, registered model settings, role instructions, request hashes, and permitted provider receipts. Keep raw inputs and outputs private. The file alone does not authenticate its producer. `exhibit_saved` requires accepted loader and registrar actions. It accepts an explicit empty message, not an absent registrar. It replays that same accepted prefix under both transport rules. No model receives a request. The new private directory contains `shown.request.jsonl`, `masked.request.jsonl`, and `receipt.json`. The receipt records hashes, lengths, and the exact number of changed digits. For a future isolated receiver, deliver only its request, not the directory, filename, or other arm.

`exhibit_options` enumerates legal depot files conditional on the actual registrar filing. It reports attainable true coverage and the true range across all files tied for the best reported weight. It measures coverage over **all** forecast briefs. With no positive delivered weight, that ranking is unavailable, not zero. A missing depot action stays null. An accepted empty file has measured zero coverage. A second ranking requires both crate/epoch entries in the depot’s visible flat agenda. In this fixture the notes alone tie 2 files: their true coverage is 0/10 or 4/10. Checking the flat agenda leaves 1 top file with 4/10. The fixture selects the other file, with 0/10. In other cases individually documented crates can still form a false pair. Neither ranking models actual reasoning. A null ranking does not mean the whole view contains no useful information.

## Check what each owner adapter recorded

After a future native team has finished, keep its prepared input directory and native trial directory. Use the same task and harness revision. This command only reads the archive. It needs the fork’s native Python environment. It does not need running sockets, a provider key or the original machine paths.

```sh
python -B -m examples.seatlab.exhibit_team_receipt "$work/inputs" authored "$archived_trial"
```

Set `archived_trial` to the retained trial directory. Use `author-masked` for that arm. The reader checks both prepared inputs, their native locks, the role instructions, the case, the independent verifier output, and each accepted owner action. It reconstructs the exact request bytes for each accepted turn. The loader’s second request must contain its original first action, not the world’s normalized copy.

The output contains hashes, aggregate counts and coverage. It does not print a private prompt, route or endpoint. `prescribed_handoff` is what the world would present after the registrar files. `depot_request_matched` requires a matching accepted depot turn. A missing depot is not an empty depot file. An incomplete team has null native reward. Missing files or inconsistent evidence return exit code `2`; neither is a model failure. The report establishes local consistency only. It does not authenticate an operator, a model, remote delivery or reading. It does not count unaccepted requests.

The engineering fixtures use the native config, lock and result serializers. A separate fixture checks exact request bytes on local sockets. These checks do not launch Harbor or run a model. Older preflights that lack the current harness digest must be read with their original revision.

## 1. Information and actions

The loader holds its forecast briefs and weights. The registrar receives the nominated briefs, with their original weights, and holds the private exhibit-to-crate history.
Only the registrar receives both sources. The loader does not receive the registrar's private crate mapping before filing.
The depot holds the private crate-to-bay history. It receives the registrar's handoff, not the original forecast or mapping.
The realized lottery brief remains hidden until both archives have filed.

The registrar files its normal cards and authors up to 4 separate claims in one bounded decision.
Each claim contains an epoch, an unordered pair of distinct crate IDs, and a one-digit weight (0–9; `0` means unknown).
A claim means that a forecast brief maps to that pair at that epoch and has the stated weight.
The schema permits an empty list. The registrar may omit a claim if it cannot establish the mapping.
A well-formed claim can be false. Syntactic validation must not require truth or reveal whether a claim is true.
Reject malformed claims under an identical validation rule in both arms. Reject duplicate epoch/pair rows. A rejected submission does not create a filing.

- `authored`: deliver the canonical accepted claims with the submitted weights, including incorrect pairs or weights.
- `author-masked`: deliver those claims with positive weight digits replaced by `0`. An existing unknown remains `0`.

The world never supplies missing claims, substitutes true weights, repairs pairs or filters claims based on source truth.
Keep the prescribed card filings and deliveries. Do not derive an additional correct weighted bundle for the depot.
Both arms use the same flat agenda rule. Its contents depend on the accepted filings.
The existing `bundled` and `masked` arms use world-derived pairs and weights. Here the sender chooses and writes the claims.
A world-authored bundle is not evidence that a registrar can construct that bundle.

## 2. Worked museum example

Four exhibits map to crates A, B, C and D in epoch 1. The relevant forecast consists of two matchings.
The depot can file only 2 crate-to-bay cards. The registrar can file 4 exhibit-to-crate cards.

| Crate pair | Layout a weight | Layout b weight |
|---|---:|---:|
| AB | 4 | 1 |
| CD | 4 | 1 |
| AC | 1 | 4 |
| BD | 1 | 4 |

Each crate has total demand 5 in both layouts. Total forecast weight is 10.
Suppose all 4 true registrar cards are filed. A faithful claim list in layout a is:

```text
1 AB 4
1 CD 4
1 AC 1
1 BD 1
```

The authored receiver can choose A and B (or C and D). The author-masked receiver sees the same 4 lines with weights `0`.
In layout b the faithful list exchanges the 4s and 1s. The respective heavy pairs are AC and BD.
Filing a heavy pair gives explicit complete-chain coverage C = 4/10. Filing a light pair gives C = 1/10.
Both have per-item coverage P = 10/20 under this complete registrar file.

These calculations assume particular correct filings. They are not scores from agents.
A fixed, layout-independent complete list and fixed prefix yield the same masked input across the 2 layouts.
For that restricted problem, a common legal depot file supports at most one listed pair and has average C at most 5/20.
A fully informed policy can attain 8/20. Neither figure bounds an unconstrained sender or a live team.
For example, a registrar can select only AB and CD in layout a, and only AC and BD in layout b.
The selected pairs disclose the heavy matching even when every weight is hidden.
Card selection can also indirectly communicate. The registrar can use the nominated weights when choosing which pairs to report.
Masking the numeric field therefore does not remove all information about weights.

## 3. Experiment A: paired saved-message receiver diagnostic

Register a concealed case roster, balanced layout assignments and a fixed legal upstream prefix before receiver calls.
Nominate all 4 briefs and file all 4 applicable registrar cards. Preserve the full original forecast for source auditing.
Collect one bounded registrar message per case in a neutral authoring setting, without revealing the lottery outcome.
Save the accepted claims, author input and prefix before any depot calls. Retain empty and incorrect messages.
For each case, replay that exact message to a fresh depot in both arms. Change only the numeric weight field, not the instruction or interpretation.
The registrar does not regenerate the message for the second arm. The depot chooses its own file in each arm.
Use identical private depot histories, quotas and initial context. Counterbalance order and reset contexts, tools and memory.
Stop after the depot file. Independently reconstruct C and P from both accepted filings, using true forecast weights.
The primary outcome is paired C(authored) minus C(author-masked). P and filing choices are secondary.
Retain all enrolled messages, including incorrect and empty ones. Report omitted pairs and wrong weights separately.
This estimates the incremental value of disclosing author-written weights for that saved-message distribution.
It does not estimate changes in author strategy. It measures no fresh whole-team native grade.

## 4. Experiment B: live 3-role teams

Use a separate concealed roster and independent fresh contexts for every role and arm.
The loader nominates up to 4 briefs. The registrar chooses up to 4 cards and 4 claims in one submission.
The depot chooses up to 2 cards. The loader receives the realized brief and commits exactly 2 routes.
Keep these four action opportunities, role access boundaries and all budgets identical across arms.
Use the same role instructions and do not announce the assigned arm. The registrar can choose which claims to send in either arm.
No extra text channel, feedback rounds or communication after the depot filing. Bound reads, retries and total turns.
Pair the generator state and precommitted weighted lottery draw across arms. Allow all role decisions to differ.
The primary outcome is paired native grade difference, with operational success and missingness reported separately. Grade 1 requires both routes to be correct; otherwise grade 0.
Do not weight that grade again. Report C and P before the reveal as separate mechanism outcomes.
This is the total effect of the declared delivery mode, under stochastic nomination, authoring, filing and dispatch. It is not a randomized disclosure to the author.
Do not condition the primary estimate on successful authoring, identical filings or receiver access.

## 5. Evidence and explanations

Use true epoch-valid source records to audit each claim's pair and weight separately.
Report represented nominated pairs, omitted nominated pairs, pairs outside the nominations, and incorrect weights. Report documented pairs separately. A correct claim can lack a filed card; a claim outside the nominations does not prove dishonesty.
Report empty lists, partial lists and explicit abstentions separately. An omission does not establish intent.
`exhibit_options` enumerates the legal depot files to compute attainable true C under that prefix.
It scores files using the weights as delivered, not corrected claims. It separately scores files using claims backed by the visible flat agenda.
If several files tie, report their true coverage range rather than selecting the favorable tie after observation.
Compare actual depot choices with these references. Keep source truth, delivered text, visible cards and observed reads distinct.

A sender omission explanation predicts that the needed claim is absent from the authored message.
A trace with faithful, delivered claims sufficient to identify an attainable better file falsifies that omission explanation for that choice.
A receiver misuse explanation predicts a worse file despite such sufficient delivered information and legal capacity.
A receiver that chooses an optimal true file does not exhibit that failure, even if some claims are omitted.
If no better file is possible under the accepted registrar prefix, attribute no shortfall to the depot's allocation.
Missing or misleading claims, uncertain ties and inadequate filings can leave the explanation unresolved.
A poor masked choice alone cannot distinguish omission from misuse. The saved-message contrast helps isolate the weight field.
A loader may infer a correct unsupported route or fail despite complete support. Native grade alone distinguishes none of these mechanisms.
Do not infer cognition, intent or internal reasoning from the choices, traces or grades.

## 6. Registration and failure accounting

Before enrollment freeze revisions, role prompts, renderer, actual serialized transport, model routes, budgets and analysis rules.
Audit visibility, source independence, weight masking and native closure on separate development cases.
Preserve exact original and delivered claims, accepted action receipts, served identities and private scoring inputs.
In experiment A match message widths and inspect all delivered bytes for unintended differences. In B report endogenous widths separately.
Use the independent base case as the sampling unit. Report paired means, layout strata and case-level uncertainty intervals.
Declare sample size and stopping rules in advance. Keep every enrolled case in the accounting.
A legal empty file has zero C if no complete chain exists. A missing filing leaves C unknown.
A valid incomplete team has null native grade and zero operational success. Missing or invalid evidence remains unknown.
Report arm-specific counts of abstentions, rejected claims, timeouts, incomplete sessions and measurement failures.
Report worst/best paired bounds for unknown outcomes. Do not silently replace cases or count malformed evidence as model failure.
Follow the [coupling protocol](PAIRING.md) for case-level inference. State the roster, sample size and stopping rule before collecting any decisions. For each distinct study, require the lower bound of the registered 95% paired interval to exceed zero to support an improvement. If the upper bound is at or below zero, the improvement is not supported at that resolution. Otherwise report an inconclusive comparison. No empirical claim follows from this development certificate.
