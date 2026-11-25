# Check a cooperative decision before measuring agents

Use this tool to check whether a declared role observation permits the best decision.
It calculates exact values on a finite distribution of task worlds and decision rules.
It starts no Harbor trial, model, container or service. It needs only Python 3.12.

## Native examples

From the repository root:

```sh
python3.12 -BS -m examples.seatlab.decision_boundary native --seed 7
python3.12 -BS -m examples.seatlab.decision_boundary native exhibit/faithful/masked --expect-gap positive
python3.12 -BS -m examples.seatlab.decision_boundary native ship/original-delivered --expect-gap zero
```

The seed selects the Exhibit Relay card IDs. Ship Handover uses its pinned, paired reference strings for every seed. Neither adapter samples model behavior.
Each study has its own fixed prior, score definition and common rule menu.
The default JSON report contains aggregate values under `studies`.
It does not print role instructions, private state labels or reference strings.

## Read the certificate

For world `w`, let `m(w)` be its positive mass and `s(w,a)` its integer score under rule `a`.
Normalize by total mass times the score scale. Partition the worlds by literal equality of role input bytes.

- `unobserved`: maximize the weighted sum with one fixed rule for all worlds.
- `observed`: maximize the weighted sum separately in each byte class, then add the maxima.
- `informed`: maximize the score separately in each world, then take the weighted sum.
- `observation_value`: `observed` minus `unobserved`, the gain available from distinguishing the declared role inputs.
- `gap`: `informed` minus `observed`, the cost of the observation restriction within the declared rule menu.
- `ambiguous_classes`: count the byte classes where separate world maxima yield strictly more value than any common rule.

The five value fields are exact reduced fraction strings. Always check `worlds`, `observations`, `actions`, `mass` and `scale` with them.
A class can contain different worlds without a positive gap if those worlds share an optimal rule.
In particular, `unobserved <= observed <= informed`; more distinct observations need not increase the optimum.

## Exhibit Relay: missing relationships or weights

See the [task](../../tasks/exhibit-relay/README.md) and [registrar-authored handoff](../../tasks/exhibit-relay/AUTHORED.md).
Use 2 equally weighted twin layouts, fixed loader nominations and a complete registrar filing.
The depot can file up to 2 visible cards. The score is true complete documentary coverage weight out of 10, not final dispatch reward.
The four true briefs have weights 4, 4, 1 and 1. The twin layouts exchange the heavy and light relationships.
Each individual optimum covers weight 4. The best common file covers total weight 5 across both layouts.

The faithful authored notes report all relationships and their true weights. Masking replaces reported weights with zero.
The heavy-only author instead reports only the heavy relationships. The empty author reports none.
A mask of the heavy-only report can still disclose which relationships the author selected.
An empty report cannot disclose the relationships through its weights.
These are fixed authoring policies, not measured sender decisions.

## Ship Handover: opportunity versus information

See the [task](../../tasks/ship-handover/README.md) and [relief instructions](../../tasks/ship-handover/instructions/relief.md).
The score is 1 if exactly one parcel ships, otherwise 0. The original either already shipped or did not ship.
The declared replay includes the relief instructions and the ordered handler replies to `GET /brief`, `/status` and `/inbox` before resolution. It does not include HTTP headers, a model chat or an early move before handoff.
It pairs the visible token strings across worlds and keeps the decision rules the same within each study.

A visible reference to the original lets the relief worker resume it without duplicating shipment.
A fixed rule can consume the visible inbox token. It need not choose different rules for different observations.
With no token, the receiver can stop, close, or dispatch fresh. Stopping and closing both win only if the original already shipped. A fresh dispatch wins only if the original did not ship.
When the inbox token can refer to either original or draft, close wins in the 2 committed worlds.
Fresh wins in the 2 dropped worlds. Resuming the returned token wins in 3 of the 4 worlds.
The informed optimum is 1 in each study: close is sufficient for committed and fresh for dropped.

Expected values derived from the native development cases and declared replay (not observed team results):

| Study | Unobserved | Observed | Informed | Gap |
| --- | ---: | ---: | ---: | ---: |
| `exhibit/faithful/shown` | 1/4 | 2/5 | 2/5 | 0 |
| `exhibit/faithful/masked` or `exhibit/empty/{shown,masked}` | 1/4 | 1/4 | 2/5 | 3/20 |
| `exhibit/heavy-only/{shown,masked}` | 1/4 | 2/5 | 2/5 | 0 |
| `ship/original-delivered` | 1 | 1 | 1 | 0 |
| `ship/original-withheld` | 1/2 | 1/2 | 1 | 1/2 |
| `ship/ambiguous-delivered` | 3/4 | 3/4 | 1 | 1/4 |

The delivered original removes the need to identify the prior. It changes the available decision rules, not necessarily information about the prior.
Do not attribute the difference between delivered and withheld only to information. Their rule menus differ.
Within a fixed menu, the gain from distinguishing byte classes is `observed - unobserved`.

## Inspect your own finite worlds

```sh
python3.12 -BS -m examples.seatlab.decision_boundary inspect - --expect-gap positive <<'JSON'
{"schema":"seatlab.decision-worlds/v1","scale":1,"worlds":[
  {"name":"case-a","mass":1,"observation_b64":"eA==","scores":{"left":1,"right":0}},
  {"name":"case-b","mass":1,"observation_b64":"eA==","scores":{"left":0,"right":1}}
]}
JSON
```

Both observations decode to the same byte `x`. Under `input`, expect `observed` = `1/2`, `informed` = `1`, and `gap` = `1/2`.
Repeat that command with `--expect-gap zero` instead. It deliberately exits 1 because the expectation is false.
For a saved JSON file use `inspect FILE`. Exit 0 means valid input and any supplied expectation met.
Exit 1 means a contradicted expectation. Exit 2 means malformed input.
Use positive integer masses and scale, unique world names, base64-encoded observations (empty is valid) and identical rule keys across all worlds.
Scores must be integers between 0 and scale, inclusive. JSON booleans are not integer scores.

## Register another study

1. Select the decision point, fixed prior actions, finite world distribution, masses and score scale before measurement.
2. Define a common menu of semantic decision rules. Specify how each rule uses visible inputs, including conditions for using a returned token.
3. Capture literal role input bytes, including instructions, ordered prior actions and replies visible to that role. Do not sort away meaningful order.
4. Pair nuisance tokens, run identifiers and other irrelevant visible strings across worlds. Document the serialization boundary and what the role cannot see.
5. Apply each rule to a fresh independent copy of each world. Score its outcome with the task semantics, independently of the report or message claims.
6. Check the certificate against known positive and zero gap controls. Register the distribution and rules before agent measurements.

A positive gap is a finite decision bound, not a claim about all possible actions, timing, communication or prior histories.
The native adapters check declared role input bytes, not whole provider prompts or unrecorded observations.
A zero gap does not establish that an agent finds an optimal rule, understands a message or uses its information.
The certificate measures neither agent reward, causal effects on agent behavior, reasoning, collaboration quality, nor runtime cost.
Changing the prior, serialization, authoring policy, available rules or target score can change the certificate.

## Show why the gap is positive

Add `--witness` to `native` or `inspect`. A positive gap returns zero-based input rows in one identical-observation class. Each listed row removes at least one possible common optimal rule. The final count is zero. The witness need not be the smallest such set. A zero gap returns `null`. The report does not show action labels or private prompt bytes.

## Test a proposed channel change

```sh
python3.12 -BS -m examples.seatlab.decision_boundary compare \
  --native exhibit/faithful/masked exhibit/faithful/shown \
  --expect-relation refinement --expect-gain positive
```

The faithful handoff separates the two layouts. Its optimal coverage rises by `3/20`. Repeat with `exhibit/heavy-only/masked` and `exhibit/heavy-only/shown`. The partition is then `equivalent` and the gain is `0`, even though both packets change. Comparing `ship/original-withheld` with `ship/original-delivered` exits 2: the extra `resume_received` rule changes the decision problem.

Use `compare --files BEFORE AFTER` for your own case tables. A comparison requires the same world names, masses, score scale, rule labels and per-world scores. Table order may differ. Identical rule labels must also mean the same executable rule; the author must check that. The tool refuses a changed score matrix instead of reporting a gain.

`relation` describes the **after** partition relative to the **before** partition. `refinement` splits old classes without merging any. `coarsening` merges without splitting. `equivalent` preserves every equality relation, not necessarily the bytes themselves. `incomparable` does both. `same_bytes` counts worlds with literally unchanged inputs. A new nonce per world can create perfect separation without a usable signal: pair nuisance bytes and repeat the check across independently generated development cases.
