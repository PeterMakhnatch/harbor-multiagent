# Does asking for a shorter sales report help the team?

## Live result: more reports reached finance; profit remains unresolved

**Asking for 100 characters delivered 24 of 24 reports. Asking for 160 delivered 13 of 24.** Both arms used the same 160-character channel. The 48 teams ran on 24 matched companies, with separate live requests for sales and finance. We kept every first attempt.

| Result | Asked for 100 | Asked for 160 |
|---|---:|---:|
| Report reached the finance model request | 24/24 | 13/24 |
| Verified sales reply above the channel limit | 0 | 10 |
| Sales request with no verified reply | 0 | 1 |
| Finance seat committed a price (native grade 1) | 23/24 | 23/24 |

The shorter prompt alone succeeded on 11 companies. The longer prompt alone succeeded on none. The [registered](https://github.com/PeterMakhnatch/harbor-multiagent/blob/21f584704080d98edbbd925d6c9ab1f9551e9567/examples/seatlab/report-budget.md) one-sided paired test gives **p = 1/2,048**. Three short-arm reports exceeded 100 characters and still passed. Asking for less left room for mistakes; it did not change the parser. Finance saw `null` on all 11 missed handoffs and still chose a price in 10.

**Do not claim a profit gain.** Both arms committed prices on 22 companies. The shorter arm earned more on 8, less on 2, and the same on 12. Mean short-minus-long operating profit was **+$43,738.05** on those 22. The short-arm finance request on company 1 and the long-arm request on company 10 returned no verified price. The [economic decision rule](#registered-plan) required all 24 pairs. It did not pass. The equal native grade totals conceal the difference in delivery; neither grade measures business profit.

**What another operator can use today.** Keep the native acceptance check separate from proof of what the receiver was sent. Record the receiver's price even after a rejected handoff. From this checkout, reproduce the [registered result](results/report-budget/result.json) without Docker, a model, NumPy or a key:

```sh
python3.12 -BS -m examples.seatlab.workbench verify native-report-budget
```

The [48 public rows](results/report-budget/rows.json) retain the synthetic first replies, rejected reports, native grades and actual stock settlements. The [stock replay](results/report-budget/replay.json) rebuilt 24 companies, 96 role prompts and all 46 committed prices. It rejected a finance prompt from the other arm and a one-cent change to a settlement in separate negative checks. The [format diagnosis](results/report-budget/diagnosis.json) shows every over-limit reply. Across 96 subscription requests, 93 verified receipts report 800,040 client tokens. Usage for 3 requests is unknown. No paid API or rented infrastructure was used. The client requested `gemini-4-carbon-low`; it did not attest the served model.

### Could the 2 missing prices reverse the profit difference?

**Yes.** After the live study, we checked all 9 ways the 2 missing finance seats could have priced their companies. Each had 3 legal choices. We kept the other 46 committed prices fixed. On all 24 companies, the attainable mean difference ranges from **−$36,457.19 to +$40,093.21**. If the missing short-arm seat on company 1 had raised prices, every choice by the other missing seat would have produced a negative mean. Neither seat actually chose a price.

This is a sensitivity check, not a new result for the agents. The [bounds](results/report-budget/bounds.json) list the affected companies, choices and exact amounts. An independent continuation of the stock simulator matched all 6 price branches to the cent.

```sh
"$STOCK_PYTHON" -B -m examples.seatlab.report_budget.bounds \
  examples/seatlab/results/report-budget/rows.json --stock "$BESPOKE_STOCK"
```

**Next question.** Finance followed the sales recommendation on 34 of the 36 delivered reports that it priced. That is not evidence that the recommendation helped. Give matched finance seats the same verified sales facts. Reveal the seller's original price recommendation to only one. Measure the price each seat chooses and settle it against the same company. Do not change the channel limit again to answer that question.

## Registered plan

**Decision.** Should a Harbor task ask for fewer characters than its delivery channel accepts? In the [previous Bespoke study](price-timing.md#live-result-more-agreement-no-established-profit-benefit), 4 first sales replies had valid prices but explanations of 161–172 characters. The maximum was 160. This study does not edit those replies or reuse those companies.

**Arms.** On each new company, run 2 separate native Harbor teams. Each has a sales seat and a finance seat. Sales gets identical private observations in both arms. One prompt asks for at most **100 characters** of explanation; the other asks for **160**. That numeral is the only change to the sales prompt. Both channels apply the same 160-character parser. A 101-character reason is eligible in either arm. Never truncate, repair or resend a report.

Finance does not see the prompt target. It receives the same financial observations plus the accepted price recommendation and explanation, or `null` if sales did not produce an accepted report. Finance gets one price choice even after rejection. Neither seat sees the other arm. There is no acknowledgment turn, second price choice, predetermined trading decision on model failure, or hindsight answer in any seat prompt.

**Companies and call budget.** Use 24 new companies, indices 0–23 inclusive. Repeat the stock market shapes `mnl`, `nested`, `wom`, `delayed_ads`, `deal_seekers` in that order. Hash `seatlab/report-budget/v1:{world}:world` and `seatlab/report-budget/v1:{world}:truth` as UTF-8 SHA-256 hex to seed the stock simulator. Use Bespoke revision `dbec43ff757c86c17077e61b37c7f584c880bdee`, NumPy 2.1.3 and the unchanged first 90 days under `plain_rules`. Each paired company—not each seat—is one unit. Even companies start with 100; odd companies start with 160. At most 2 local native trials run at once. Company 24 is a no-model control, outside the panel.

The maximum is 48 native teams and 96 first model requests. Use the same personal subscription client as the first study: `agy` 1.5.15, requested `gemini-4-carbon-low`, tools disabled, no paid fallback or API keys. Each turn needs its own admission and original request. Credits must be zero and both quota windows above the client's 10% floor. Stop issuing new teams after an admission refusal or 2 successive wholly unsent failures other than a company ending before day 90. Preserve in-flight teams and every open slot. Never switch providers inside this cohort. Client-reported tokens are not a provider bill.

**Primary: did the report reach finance on the first attempt?** Count success only with a verified live sales answer, native acceptance, a matching finance view, and a recorded finance request containing that exact report before pricing. This tests delivery to the model client, not whether the model understood the report. A verified rejection is a delivery failure. A sent sales request that ends unanswered can be a known failure if the native evidence confirms that no report passed. Missing evidence, no admission, a predecision closure, or an unissued team is unknown, not a zero. A finance reply is not required to prove a submitted request.

**Pass/fail.** Require 24 matched pairs with known delivery outcomes. Count companies where only 100 delivered and companies where only 160 delivered. Use the one-sided exact paired binomial test on those discordant companies, with null probability 0.5. A delivery gain requires more 100-only companies and `p ≤ 0.05`; no discordance gives `p = 1`. An incomplete panel is inconclusive. Do not choose a smaller denominator after seeing the data.

**Secondary: did the price decision still earn money?** Independently continue each actually committed price through closure or day 1,095. Measure simulated operating profit after day 90, including failed-report branches in which finance still priced. Report paired short-minus-long profit, wins, losses, ties, and the number of missing prices. Never give a missing price a zero-dollar outcome. Recommend the shorter target for this cohort only if the primary passes, all 24 price pairs settle, and observed mean profit is not lower. That safeguard is not an equivalence or non-inferiority test. Measure model requests, receipts and known tokens separately from money.

**What other operators can verify.** Keep first requests and receipts in a private owner directory. Release only synthetic reports and decisions, role and prompt hashes, verified client usage, native outcomes and stock settlements. A portable summary must run without a model, Docker, or a paid key. A second operator with the pinned simulator must be able to rebuild the prompts and settlements. The controls must show both paths: a valid report reaches finance, and a rejected report does not, while finance can still commit a real price in both.

## Run a control or a new live team

Use [the study branch on Peter's fork](https://github.com/PeterMakhnatch/harbor-multiagent/tree/experiments/seatlab-report-budget). Set `STOCK_PYTHON`, `BESPOKE_STOCK`, `HARBOR_PYTHON` and `SLOT_WRAPPER` as in [the native setup](price-timing.md#run-one-new-comparison). Use the same 2-slot limiter across all checkouts on one host. Do not install a global Harbor binary. The stock Python needs NumPy 2.1.3; the native Python needs the fork's Harbor runtime. Run the commands from the checkout root.

Create a private output directory. It holds owner-side request and receipt files. Do not publish that directory.

```sh
OWNER_ROOT="$(mktemp -d)"
```

First run both native controls. They start Docker but never start a model client:

```sh
for target in 100 160; do
  "$STOCK_PYTHON" -B -m examples.seatlab.report_budget.study run \
    --stock "$BESPOKE_STOCK" --root "$OWNER_ROOT" --world 24 --target "$target" \
    --native-python "$HARBOR_PYTHON" --slot "$SLOT_WRAPPER" --control
  "$STOCK_PYTHON" -B -m examples.seatlab.report_budget.study inspect \
    --stock "$BESPOKE_STOCK" --root "$OWNER_ROOT" --world 24 --target "$target" --control
done
```

In the first control, sales sends 100 characters and finance raises prices. In the second, sales sends 161 characters. Finance must see `null` and keep prices. A protocol grade of 1 in both controls means finance committed a valid price in each. It does **not** mean both reports passed.

To run new teams with the registered personal subscription client, set `AGY_CLIENT` to its absolute executable path:

```sh
"$STOCK_PYTHON" -B -m examples.seatlab.report_budget.campaign \
  --stock "$BESPOKE_STOCK" --root "$OWNER_ROOT" --native-python "$HARBOR_PYTHON" \
  --slot "$SLOT_WRAPPER" --client "$AGY_CLIENT"
```

The two workers take disjoint companies in fixed order. Each attempt owns one directory. A second process cannot replace an issued attempt. A provider refusal stops new work; already issued attempts remain in the release. To assign one company manually, use `study run` with `--world 0` through `23`, the chosen `--target`, and `--client "$AGY_CLIENT"` instead of `--control`. There is no need to run a live client to inspect existing evidence.

```sh
"$STOCK_PYTHON" -B -m examples.seatlab.report_budget.study export \
  --stock "$BESPOKE_STOCK" --root "$OWNER_ROOT" > "$OWNER_ROOT/public-rows.json"
python3.12 -BS -m examples.seatlab.report_budget.panel "$OWNER_ROOT/public-rows.json"
"$STOCK_PYTHON" -B -m examples.seatlab.report_budget.replay \
  "$OWNER_ROOT/public-rows.json" --stock "$BESPOKE_STOCK"
```
