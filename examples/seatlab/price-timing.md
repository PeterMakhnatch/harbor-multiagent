# When should sales tell finance what customers are buying?

## Registered comparison — November 23, 2026 ET

**Question.** Does a sales report improve the profit of a simulated company when finance receives it before it commits a price rather than after? This is a new live experiment on Bespoke's *Seed to Profit* simulator, not a replay of the [older market choices](market-handoff.md).

**Intervention.** Advance each company through 90 days under the same stock `plain_rules` policy. A live sales agent sees only the sales observations. It sends a price recommendation and a reason of at most 160 characters. Fork that company and that exact report into 2 finance seats. Each finance seat gets the same financial observations, chooses one price policy, then acknowledges a delivery with no right to revise its price. Early finance receives sales's report at the price turn. Late finance receives the identical report at the acknowledgment turn, after the world accepts the price. The other delivery slot is empty. The sales call is shared within a pair; it is not counted as two independent calls. The report, finance decisions and acknowledgments all come from live subscription sessions. The acknowledgment receives the finance seat's own decision history.

**Action budget.** Each finance seat makes 1 price decision and 1 receipt turn. The available price multipliers are 1.00 (keep), 1.15 (raise), and 0.85 (discount). They scale the same public reference pricing policy across products. All other operations keep the plain policy. The host simulates *both* committed prices separately to day 1,095 or bankruptcy, including equal picks. A stock price table cannot substitute for a continuation. There are no extra tools, replacement replies, decision retries, paid API calls or Codex seats. Equal turns do not imply equal token usage. Record what the signed-in client reports; do not infer a provider bill.

**Panel.** Use fresh company indices 0–7. For index `w`, hash the ASCII strings `seatlab/price-timing/v1:{w}:world` and `seatlab/price-timing/v1:{w}:truth` with SHA-256. Pass each lowercase hex digest to the stock constructor as its seed. Shapes in order: `mnl`, `nested`, `wom`, `delayed_ads`, `deal_seekers`, `mnl`, `nested`, `wom`. Keep stock revision `dbec43ff757c86c17077e61b37c7f584c880bdee` and NumPy 2.1.3. Use repetitions 0 and 1. Each repetition gets a new sales session and two new finance decisions. In repetition 0, `finance_a` gets the early delivery when the first byte of SHA-256(`seatlab/price-timing/v1:{w}:assignment`) is even; otherwise `finance_b` does. Reverse the assignment in repetition 1. Use company 8 (`mnl`) only for a native plumbing control with fixed replies. Exclude the control from all behavioral counts. Do not inspect a fresh company's profit to select it.

**Outcomes.** Primary: the difference in realized operating profit in simulated USD from day 90 to the stock terminal day (early minus late). Sum the stock monthly `profit` records after the first 3 complete 30-day months. This metric includes accrual operating costs, not interest, tax, or financing charges. Do not substitute cash, owner valuation or a normalized score. A bankrupt branch keeps the actual operating profit it earned up to closure. Also report total operating profit, terminal owner value, bankruptcies, the stock score, and whether the finance choices or agreement with sales differ. Native protocol reward measures whether the handoff finished; it is not company profit.

**Pass/fail rule, fixed before the first live turn.** Require both valid deliveries, actual acknowledgments, verified subscription sessions, and fresh stock settlements for every registered pair. Average the 2 profit differences within each company, then weight the 8 companies equally. Count a benefit only if the mean is positive, at least one price choice differs, and the one-sided exact sign test across companies gives `p ≤ 0.05`. Drop exact zero company differences only from that sign test; if all tie, use `p = 1`. This tests a consistent positive direction under independent equally likely signs, not the magnitude of a population effect. A nonpassing complete panel means this experiment does not establish a benefit. A pre-checkpoint bankruptcy, invalid reply, missing verified receipt or ungraded pair makes the registered benefit test inconclusive. Keep every first attempt. Show observable price commitments even if a later acknowledgment fails. Never price a missing branch as zero or claim that missing token usage is zero.

**Scope.** This is a restricted price-setting task in the released stock simulator. It is not the published open-ended benchmark, an estimate of profit for real retailers, or 32 independent companies. The two finance continuations in each pair implement alternative futures, not two directors jointly running one company.

## Run one new comparison

Use the study branch on [Peter's fork](https://github.com/PeterMakhnatch/harbor-multiagent/tree/experiments/seatlab-live-price-timing). The upstream repository and the fork's default branch do not contain this study.

```sh
git clone --branch experiments/seatlab-live-price-timing --single-branch \
  https://github.com/PeterMakhnatch/harbor-multiagent.git
cd harbor-multiagent
```

Use Python 3.12 with NumPy 2.1.3 for `STOCK_PYTHON`. Point `BESPOKE_STOCK` at the [released benchmark checkout](https://github.com/bespokelabsai/multi-agent-coordination-bench/tree/dbec43ff757c86c17077e61b37c7f584c880bdee) at the registered revision. Use the [fork's isolated native setup](../../README.md#seatlab-workbench) for `HARBOR_PYTHON`. Do not replace a global Harbor installation. Start Docker locally.

Set `SLOT_WRAPPER` to `/tmp/crucible-docker-slot.sh` on Peter's study host. On a different host, all workers must use the same limiter. If that host has none, use the [limiter included in this fork](workbench/local-slot): `export SLOT_WRAPPER="$PWD/examples/seatlab/workbench/local-slot"`. It shares 2 slots across checkouts for one account. Do not mix the two limiters on one host.

The native control needs **no model client, subscription, or API key**. Only the live seats require personal `agy` 1.5.15 with `gemini-4-carbon-low`. Each live turn checks the route, client version, zero spendable AI credits, and remaining quota. It denies tools and paid fallback. The client runs in an empty workspace. Never put credentials in the task, trial config, or Docker environment.

```sh
m=examples.seatlab.market_handoff
root=$(cd "$(mktemp -d)" && pwd -P)
"$STOCK_PYTHON" -B -m "$m.timing_study" run --root "$root" \
  --stock "$BESPOKE_STOCK" --world 8 --repeat 0 --control \
  --native-python "$HARBOR_PYTHON" --slot "$SLOT_WRAPPER"
```

The [original control](results/price-timing/control.json) and a [fresh control with no client configured](results/price-timing/control-no-client.json) each scored 1 for completing the handoff. The fixed raise earned $274,775.27 in simulated operating profit after day 90. The fixed discount lost $327,660.86. Both attempts used company 8, outside the analysis. Neither made a model call.

For a **new live team**, replace `--world 8 --repeat 0 --control` with `--world 0 --repeat 0 --client "$(command -v agy)"`. For the full design, issue worlds 0–7 with repetitions 0 and 1. Two operators can share `root` and issue distinct cells. A cell refuses a second launch. Keep the private root; it contains the native trial and original client receipts.

To inspect an existing cell without starting seats or Docker, run:

```sh
"$STOCK_PYTHON" -B -m "$m.timing_study" inspect --root "$root" \
  --stock "$BESPOKE_STOCK" --world 0 --repeat 0
```

## Collect or replay without a model

After your workers finish, export their original attempts and score the panel. Neither command starts Docker or calls a model. The exporter reads all 16 slots. It distinguishes missing attempts, incomplete runs and completed runs. It never replaces a missing grade with zero.

```sh
m=examples.seatlab.market_handoff
"$STOCK_PYTHON" -B -m "$m.timing_study" export \
  --root "$root" --stock "$BESPOKE_STOCK" > "$root/rows.json"
python3.12 -BS -m "$m.timing_panel" "$root/rows.json"
```

To check the business result, use the actual stock simulator. This command reconstructs the private views from public seeds, checks the recorded replies and their delivery order, then trades both committed policies to the terminal day. Add `--world 3` to inspect only company 3. A partial price commitment can be replayed, but it does not become a complete team result.

```sh
"$STOCK_PYTHON" -B -m "$m.timing_replay" "$root/rows.json" --stock "$BESPOKE_STOCK"
```

The public rows contain synthetic reports, choices and hashes. They do not authenticate a remote machine or a model provider. An owner with the original private directory can additionally verify the native result and client receipts with `inspect`.

## Can the sales recommendation beat simply keeping prices?

This **secondary diagnostic** was specified after the trials started, before inspecting their prices or profits. It cannot change the registered verdict. Rebuild each of the 8 companies and simulate all 3 prices from its day-90 checkpoint. Compare the reported prices with (1) always keeping prices and (2) the most profitable of the 3 prices in hindsight. Report ties as ties. For each complete pair, show what following sales exactly would have earned relative to keeping prices. A hindsight choice measures room to improve; it is not an available agent or a description of what sales knew. No new model sessions may answer this question. Never substitute a lookup from this diagnostic for a registered branch settlement.

```sh
"$STOCK_PYTHON" -B -m "$m.timing_value" "$root/rows.json" --stock "$BESPOKE_STOCK"
```
