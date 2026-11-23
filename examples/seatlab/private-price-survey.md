# Does a private demand survey justify a pricing handoff?

The [earlier sales handoff](market-handoff.md) changed which price teams chose, but always using the stock price scored better. This study asks a different question: does changing *relative* prices across products give a research seat useful information to send to an operating seat? First test the action with exact information, before asking agents to acquire noisy information.

## Protocol fixed before scoring

Use the 24 released day-90 companies: target and donor indices 0–11. Use the stock Seed to Profit source `dbec43ff757c86c17077e61b37c7f584c880bdee`. For every branch, rebuild the prefix, match its existing bytes, replace the rule policy, and continue the stock simulator to day 1,095 or bankruptcy. Preserve the catalogue, product selection, channel mix, credit, reorder and advertising rules. Score with the official world score; this is not the full benchmark grade. Keep every zero and tie.

* `K`: keep every price at 1.00 times the typical price; use the published outcome. Re-run targets 1 and 7 and donors 9 and 11. Require the identical stored prefix bytes and exact stored K payoff on all four before running C or T.
* `C`: use the public product cost term only, at no research charge. For each of the ten active products define `c = (standard landed cost + product marketplace fulfilment + 0.1 × return shipping) / ((1 − public referral fee − 0.1) × typical price)`. Use the stock policy's public 10% return allowance. Do not use company truth in C.
* `T`: use `c + 1 / (beta × typical price)`, where `beta` is the exact stock survey price-response coefficient for this company and product. This is an **owner-only diagnostic**, not an allowed source of evidence for a model. Charge $60,000 of simulated research spend once in the official world score, the price of ten n=800 surveys. Do not also debit working capital. For C and T, subtract the mean of the ten terms, add 1.00, and clip each product to [0.85, 1.15]. Apply these multipliers every day. Clipping need not preserve the mean.

Select one free comparator for every company: the higher panel mean of K and C (K wins an exact tie). Proceed only if the mean advantage of T over that comparator is at least 0.03 world-score units, and T wins at least 3 more companies than it loses. This is a screen for this action rule, **not** an upper bound for all pricing policies. No statistical discovery claim follows from reusing these 24 known companies.

Only after that gate passes, run `S` on the same 24 companies. Buy one stock n=800 general-panel survey per product at 0.70 and 1.30 times the typical price. Use stable test IDs 1–10 in sorted product-ID order. For each price use `(would_buy + 0.5) / (asked + 1)`; estimate epsilon from the logit difference divided by 0.60, and clip it to [0.5, 6]. Apply the T rule with this estimate and the same $60,000 charge. S must beat the free comparator by at least 0.03, give a one-sided exact paired sign-test p≤0.05, and beat C by at least 0.01. Otherwise do not open live research and operating seats. An S pass is a reason to test a team, not a measured model effect.

## Result: keep the free pricing rule

All four stock replays matched the original day-90 bytes and the exact stored K payoffs. The 48 new continuations ran to day 1,095. The table covers all 24 companies. These are simulated dollars and stock world scores, not model fees or the full benchmark grade.

| Price rule | Mean gross world score | Mean world score after research | Companies with zero world score |
|---|---:|---:|---:|
| K: uniform price | 0.07833 | 0.07833 | 13 |
| C: public cost terms | 0.08596 | 0.08596 | 12 |
| T: exact price sensitivity and cost | 0.09003 | 0.08777 | 13 |

The exact-information rule beat C on 5 companies, lost on 7 and tied on 12. Its charged advantage was **0.00181**, below the required 0.03. It also missed the required 3 net wins. Even before the research charge, its mean advantage was only 0.00407. We did not order noisy surveys or run agents. This rejects this pricing rule as a reason to add a research seat. It does not reject private research or pricing specialists in general.

Rebuild the [panel](results/private-price-survey/panel.json) from the 28 released simulation receipts using Python 3.12 and its standard library:

```sh
d=examples/seatlab/results
python3.12 -BS -m examples.seatlab.market_handoff.private_price_panel \
  "$d/market-handoff" "$d/private-price-survey/rows" \
  | cmp - "$d/private-price-survey/panel.json"
```

To repeat a stock continuation, use an isolated Python 3.12 with NumPy 2.1.3. `STOCK` is the pinned Bespoke checkout. This command runs one public-cost and one exact-information continuation on the same prefix. It never executes a model's reply.

```sh
"$STOCK_PYTHON" -B -m examples.seatlab.market_handoff.private_prices \
  "$STOCK" examples/seatlab/results/market-handoff exact target 1
```
