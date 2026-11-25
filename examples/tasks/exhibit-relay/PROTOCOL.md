# Exhibit Relay: prospective matched-team protocol

Status: design registration only. No model trials or empirical effects are reported here.
See the [native task README](README.md) for the executable task and reference controls.

## Question and arms

Does a weighted crate-and-epoch agenda improve physical dispatch under a fixed card quota?
The loader nominates forecast slips, the registrar files exhibit-to-crate history cards,
the depot files crate-to-bay history cards, then the loader receives the realized slip.
The loader commits exactly two exhibit-to-bay routes for that slip's epoch.
Both mappings are bijections at each epoch; the card validity intervals are inclusive.

- **Linked:** The registrar sees the nominations. The depot sees an agenda derived from
  nominated items covered by the registrar's selected cards. Each distinct (crate, epoch)
  receives the sum of its covered nominated item weights. The loader receives both files.
- **Independent:** Identical except that the depot receives an empty agenda. The registrar
  still sees nominations, and the loader still receives both files. This is the primary control.
- **Silent:** Same seats and caps; suppress nominations to the registrar, agenda and returned
  cards to the loader. This is a separate visibility boundary, not the primary control.
- **Open:** Expanded nomination and filing caps permit delivery of all relevant records.
  This is a capacity ceiling check, not a budget-matched causal contrast. A lawful policy
  that files all records and joins them by epoch should route every slip correctly.
  An unconstrained model need not achieve this ceiling; an omniscient author is not a seat.

The fork profile has 3 briefs, 2 nominations and 3 cards per source. Two briefs overlap
in one exhibit at the same epoch; the singleton brief is disjoint at another epoch.
The two weight patterns are (3,3,4) and (2,2,6). A union of 3 exhibit-epoch intents
covers weight 6 in the first pattern; the singleton alone covers weight 6 in the second.
These are feasible registrar-side portfolios, not guarantees of downstream support or reward.
Mesh has 6 briefs, 3 nominations and 4 cards per source. Analyze mesh separately.
Do not assume a universal optimal mesh portfolio.

## Assignment and execution

Primary population: fresh fork cases from the frozen generator and one declared model
roster (model, role, instructions and harness version). Use a fixed roster across arms.
Commit the generator version, concealed seed manifest digest, role budgets, sampling
settings and randomized execution order before any model call. Draw distinct case seeds
independently of model performance. Counterbalance arm order within the seed blocks.
The generator's lottery stream is separate from the other case streams and from treatment
assignment. Both arms receive the same case, including the same weighted lottery draw.
The world may compute the draw at initialization, but must not reveal it before depot filing.
Never select or replace a draw based on a nomination, card file, success or treatment.

Run fresh, isolated online teams in both arms. Match per-role turns, tools, time limits,
token and cost caps, context settings, model route and retry policy. The realized agenda
is the intended input difference; do not hold its downstream model actions fixed.
Do not replay the linked team's selections or final receiver prompt in independent.
Reset context and files between arms; no cross-arm feedback or shared memory.
Record actual served model identity, usage, exceptions, timestamps, and run/config hashes.
If routes or budgets cannot be matched, these data do not identify the registered contrast.
Use role-isolated transport and only the prescribed views and actions. Verify isolation
and native closure before enrollment. No seat sees a seed, hidden mapping or score.

## Outcomes and estimand

Let b index all forecast briefs in case c, with p(b|c) = weight(b)/sum(weight).
Let Y(a,c,b) be the binary physical reward for arm a when brief b is realized,
including the stochastic online team rollout. Reward is 1 only if both routes are correct.
The primary estimand is E_c[sum_b p(b|c) E_rollout(Y(linked,c,b)-Y(independent,c,b))].
Estimate it by the mean paired realized reward difference over independent cases.
The weighted lottery already samples p; do not multiply realized rewards by weight again.
A correct item count is secondary, not a substitute for the native all-or-nothing reward.

Independently reconstruct coverage from the canonical request and both filing events,
using the private case histories, before considering the realized ID or commit action.
For each brief, count items with the unique true epoch-valid registrar card filed and
visible to the loader, the corresponding true depot card filed and visible, and both.
Define C as sum_b p(b|c) times the indicator that BOTH items have full chains.
Define P as sum_b p(b|c) times the fraction of its two items with full chains.
These are pre-reveal coverage outcomes, not predicted physical rewards. Count all briefs,
including unrequested ones: a valid card may incidentally support another brief.
Record coverage in trusted analysis artifacts, never in a seat view. Also retain the
scorer's realized registrar_supported, depot_supported, chains_supported and
 dispatch_supported counts (both chain and correct route), as well as correct_items.

For each distinct intended crate-epoch pair in the reconstructed agenda, record its
weight and whether the selected depot file lacks its true epoch-valid card. Report the
weighted missing fraction over total agenda weight (including partial fulfillment), with
empty intent counted separately, not as perfect fulfillment. Compute this same potential
agenda from nominations and registrar files even in independent, where it is not sent.
Report its weight relative to twice the nominated brief weight, to expose registrar gaps.
Also report C and P jointly: P-C measures partial-chain-coverage mass, not full readiness.
The intended workload is treatment-dependent; missing fraction is descriptive, not a
causal effect conditional on the same intent. Record nominations, caps used and empty files.

Correct unsupported dispatches are valid successes, including lucky guesses and valid
inferences from bijections. Conversely, complete support need not produce correct routes.
Canonical ordering removes order as a message field, but selection and timing can encode
information implicitly. This is an availability intervention, not proof of internal reasoning,
intentional communication, or that a particular card causally produced a particular route.

## Decision rule and sample rationale

Directional hypothesis: linked improves mean physical reward AND mean complete-chain
coverage C relative to independent. The former is primary; the latter tests the proposed
mechanism. Register 100 independent fork cases per roster (200 team runs), no optional
stopping; separately report the two weight patterns and their enrolled counts.
At n=100, paired differences in [-1,1] have standard error at most about 0.10;
with 25% discordant pairs and near-zero mean, it is about 0.05. This illustrates
resolution, not power or an assumed discordance rate. A weak effect may remain inconclusive.
Report paired means, rates, discordance counts and two-sided 95% case-bootstrap intervals
(10,000 resamples, fixed analysis seed). Support the joint directional claim only if both
lower bounds exceed zero; an upper bound at or below zero for either endpoint refutes
that joint claim at the registered resolution. Otherwise call it inconclusive.

Each independent base case is one cluster: both arms, all repeats, counterfactual lottery
branches and seat calls remain within it. Average repeats within case before resampling.
Never treat forks, individual exhibits, or repeated model calls as independent cases.
Keep all enrolled attempts in the denominator. A valid incomplete prefix has null native
reward and null scorer metrics; count it as zero for operational team success, report its
frequency, and distinguish that convention from the native grade. Coverage needs both
filings; missing coverage is unknown, bound it by [0,1] instead of imputing failure.
Absent/malformed cuts or unmatched serving evidence are measurement failures, not model
failures. Report counts and worst/best paired bounds including all unknown outcomes;
if any measurement failures occur, the registered confirmatory decision is inconclusive.
Likewise no positive conclusion if missing coverage bounds can reverse its direction.
Do not replace failed cases or report a complete-case-only confirmatory estimate.

## Limits and evidence basis

[Revised dispatch](../revised-dispatch/README.md) separates correct action, timely receipt
and agreement; its scripted controls do not establish model behavior. The fork's
[receiver replay audit](../../seatlab/receiver-replay-fidelity.md) compares native outcomes
with fixed-prompt draws on Dispatch and Calendar, and explicitly limits their use to
screening. Hence this protocol requires matched online teams and independent outcome receipts.
Matching limits does not match actual token use: agendas alter context, latency and work.
Report these costs and visibility changes, including truncation and timeout counts.
The contrast is the effect of this structured information channel at the declared budget,
not the effect of adding agents, teamwork versus solo work, or general model capability.
No deterministic reference policy result establishes the effect for real model teams.
