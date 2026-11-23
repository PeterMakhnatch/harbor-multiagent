# Is there a business decision that needs both demand and inventory controls?

Registered before generating either panel on November 23, 2026. [The price study](market-handoff.md) found little advantage even with perfect future information. This follow-up asks whether spending on a model team has a defensible upper limit when 2 roles control different actions. This screen uses stock economics and **no model**. It is neither a native team trial nor a training set for a model.

**Population.** Use 12 calibration companies and 12 separate holdout companies. For `split` in `calibrate`, `holdout`, index 0–11 and `purpose` in `world`, `truth`, use the lowercase hex SHA-256 of `seatlab/market-roles/v2/{split}/{index}/{purpose}` as the stock seed. Keep the shape sequence `mnl,nested,wom` 3 times, then `delayed_ads,deal_seekers,delayed_ads`. Keep every company, including a bankruptcy. Never swap seeds, options or cutoffs. Use Bespoke revision `dbec43ff757c86c17077e61b37c7f584c880bdee`, Python 3.12 and NumPy 2.1.3.

**Choices.** Run the stock public rule until day 90. From the same state evaluate 9 combinations under the original world luck. Sales controls the sponsored ad budget on each of the 10 product lines: `usual` $30, `off` $0, or `push` $120 per line per day, before the stock policy's cash adjustment. Operations controls inventory: `usual` cover 30 and batch 60 days; `lean` 15 and 30; `deep` 60 and 120. Leave prices at 1.0, first order at 2 minimum orders, social ads at zero, and all other controls unchanged. Reset the rule's private history at day 90 in every option. Continue to day 1,095 or bankruptcy. No model text is executed. Score using stock `world_score` with zero research spend.

**Decision.** Find the one fixed combination with the highest calibration mean. Break ties by the order above, advertising first, then inventory. Seal this combination before running any holdout payoff. For each holdout company record (a) its grade at the frozen combination, (b) its highest grade if only one control may change from that combination, and (c) its highest grade across all 9. The one-control comparator may choose which control to adjust with hindsight separately in every company. This makes it harder to claim that both controls matter.

**Pass rule.** Require all 24 companies and all 216 outcomes. On the 12 held-out companies, require mean `c − a` ≥0.03 and mean `c − b` ≥0.01. Also require `c > b` on at least 3 of 12. A pass only justifies designing a new model study. It does not show that agents can infer or coordinate the correct actions. A fail rules out this particular operating target on these worlds, not all use of teams. Do not test another threshold or tune either action level after opening the holdout.

**Why this design.** Kim et al. [matched reasoning budgets and tool access](https://arxiv.org/html/2512.08296v3) when comparing agent configurations. They found that stronger individual baselines left less room for coordination in their settings. They did not test this business task or this ceiling. Our fixed combination gives the team an inexpensive comparator. A possible later live test would give sales only local demand evidence and operations only cash and inventory evidence. It would use new companies and equal call budgets for the joined and separated agents.

## How independent workers run this study

The workers run the stock simulator locally. They do not need an API key or Docker. Set `BESPOKE_ROOT` to the pinned Bespoke checkout. Set `STOCK_PYTHON` to Python 3.12 with the stock simulator dependencies, including NumPy 2.1.3. Use one campaign directory outside both checkouts, on the same local POSIX filesystem.

```sh
m=examples.seatlab.market_handoff.operations_work
campaign=/tmp/seatlab-operations-example
"$STOCK_PYTHON" -B -m "$m" work "$BESPOKE_ROOT" "$campaign" --split calibrate --max-worlds 12
"$STOCK_PYTHON" -B -m "$m" freeze "$campaign"
"$STOCK_PYTHON" -B -m "$m" work "$BESPOKE_ROOT" "$campaign" --split holdout --max-worlds 12
"$STOCK_PYTHON" -B -m "$m" status "$campaign"
```

For either `work` step, agents can run the same command in separate shells. They skip companies locked by a peer. Keep the directory when a worker exits. A later worker validates completed rows and can reclaim an unfinished company. Run `freeze` only after all 12 calibration rows exist. Holdout workers reject a missing or inconsistent freeze. These locks coordinate local workers; they do not authorize paid calls or coordinate separate hosts.

## Result: do not buy a two-control team for this target

All 216 stock continuations finished. The calibration panel selected the usual advertising budget and deep inventory. We [published that choice](https://github.com/PeterMakhnatch/harbor-multiagent/commit/c1d38180e455cab3da09a0ac5e10e9cf5f97b3ef) before starting a holdout worker. No company state matched the earlier pricing study.

| Choice on 12 separate holdout companies | Mean stock world grade |
|---|---:|
| Use the one fixed combination from calibration | 0.13178 |
| Know the future and change at most one control | 0.16797 |
| Know the future and choose both controls | 0.16838 |

Adapting both controls could gain 0.03661 over the fixed choice. But the second control added at most **0.00041** on average, below the required 0.01. Both controls had to change in only **1 of 12** companies, below the required 3. Six companies scored zero under every option. The one-control comparator could choose *which* control to change for each company. No agent made that choice in this screen. Actors might still need to exchange information to choose a single action.

Two independent workers used the same campaign. Each computed 6 different companies. Neither duplicated a company. A third worker validated all 12 and computed none. The [worker receipts](results/market-operations/workers.json) describe the assignments. An interrupted worker also released its lock in a separate process check.

Recompute the [result](results/market-operations/result.json) from the released rows without installing the simulator or using an API key:

```sh
d=examples/seatlab/results/market-operations
python3.12 -BS -m examples.seatlab.market_handoff.operations_grade score \
  "$d/calibrate" "$d/holdout" "$d/freeze.json"
```
