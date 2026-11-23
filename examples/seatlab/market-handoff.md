# Does a sales agent's advice help the finance agent price the right company?

Prospective protocol, 2026-11-23. No study company, sender, or receiver has run when this protocol is written. This is a **new two-seat decision task inside the released Seed to Profit simulator**. It uses stock state transitions and `world_score`, not the complete Bespoke task. It does not measure the published benchmark grade or a live Harbor session. Only stock code runs; models select menu options.

**Decision.** Use the stock `plain_rules()` policy for 90 days. The company starts with $2 million in seed funding. A sales analyst sees demand and competition. A finance director sees the company’s own finances. The analyst sends one pricing recommendation. The director chooses one continuation for all 10 product lines: (1) keep the public typical prices, (2) raise to 1.15 times typical, or (3) lower to 0.85 times typical. Other stock controls stay fixed. Each model gets one direct Flash turn, no tools, no grade, and no retry.

**Arms.** For each target company use 2 independent finance director calls with the identical local finance view. One gets a new sales agent’s advice for that company. The other gets advice from a separate company of the same market shape. The director cannot tell which producer supplied the advice. A donor is used once only. Send in mixed order, keep neutral option names and give both arms the same message format. A receiver can ignore the advice. With the same advice, repeated choices still measure model variability.

**Population.** Freeze indices 0–11 before observing any scores. For each index `i`, purpose `world` or `truth` and side `target` or `donor`, use the lowercase hex SHA-256 of `seatlab/market-handoff/v1/{side}/{i}/{purpose}` as the stock seed. Use `mnl,nested,wom` 3 times, then `delayed_ads,deal_seekers,delayed_ads`. Use a fresh product truth per company and no optional mid-run announcement. `smoke` is a distinct namespace; it is never analyzed. Keep early bankruptcies and equal outcomes; do not replace them. Keep invalid first replies in coverage. Record the actual Python, NumPy and stock source revision. The 12 pairs are the independent units, not individual messages or simulation branches.

**Primary test.** Compute the mean stock `world_score` for the target company under its own sales advice minus its score under the donor advice. Require complete valid first decisions for all 12 pairs, an improvement of at least 0.03 grade units, and a one-sided exact sign test ≤0.05 over non-tied pairs. All-tie evidence has p=1. Report the sign counts, the uniform-menu mean, always keeping prices, and the best possible final option. Report the founder-value multiple as a secondary metric; it cannot turn a failed primary test into a pass. Separately show how often advice differs, how often the director follows it, and how often the options actually have different outcomes. Stronger conclusions require fresh worlds.

**Custody.** Both roles see projections only of stock `Sim.observe()` during the first 90 days. Neither sees the hidden products, the world seed, other role inputs, grades, or future values. Score all three options by continuation from the same day-90 company state and unchanged stock luck. Use zero research spend. Do not call `start()` a second time; only attach the new stock policy. The continuation runs for the rest of the 1,095-day horizon, or to bankruptcy. Seal both sets of first replies before evaluating the payoff table. Do not execute any text from a model. No Docker, managed sandbox or new API provider is needed. The only purchase is 48 metered, bounded DeepSeek calls under the existing account floor and cap.

Source: [released Seed to Profit task and simulator](https://github.com/bespokelabsai/multi-agent-coordination-bench/tree/dbec43ff757c86c17077e61b37c7f584c880bdee/tasks/seed-to-profit). Prior experiment: [selection using the public team board](continuation-study.md). The selection study asks which recorded team to continue. This study instead asks which action two new agents should take in a fresh world.

## Native transport test (separate from the 12-pair experiment)

Question: does Olam Harbor deliver the same sales message to the finance seat and return the selected stock reward? Test world 0 with its own sales agent and world 1 with a donor. These cells are fixed before inspecting any target payoff. Start a real local Docker session for each cell. First use the sealed picks; then use one fresh direct Flash turn per seat if the shared meter permits. Do not retry a model to match an earlier choice. This is a transport test, not a second efficacy estimate.

A cell passes only if Harbor records 2 successful seats, the world records both first submissions, and the verifier returns the selected option's stock `world_score`. The sales prompt must match its sealed prefix. The finance prompt must match the target finance prefix and the sales choice **actually delivered in that session**. Both checks compare SHA-256 digests. A fresh model may change its choice without failing the transport test. Each paid cell has 2 calls and at most $0.02 reserved. Payoffs travel only with the verifier; neither seat sees them. The result page lists replay separately from new model sessions.

## Result: the correct sales recommendation did not pass

All 48 first model replies were valid. We [sealed both roles and all 24 prefixes](https://github.com/PeterMakhnatch/harbor-multiagent/commit/565d69d1d78483266b56859c415c206bd8ca4eb0) before running any target payoff. At day 90, all 3 continuations start with the same company and reset the stock policy's private history. This applies even to keeping prices unchanged. We measured the released simulator's world score with zero research spend, not the full benchmark grade.

| Decision on the 12 target markets | Mean world score |
|---|---:|
| Director with its own sales agent | 0.09468 |
| Director with the other company's sales agent | 0.09013 |
| Pick uniformly from the 3 prices | 0.08233 |
| Keep prices unchanged on every company | 0.10395 |
| Best possible price with knowledge of the future | 0.11154 |

The correct handoff won 3 times, lost once and tied 8 times. Its mean gain was **0.00455**, below the registered 0.03; the one-sided exact sign test gave p=0.3125. The advice differed in 8 of 12 pairs. Directors followed their own agent in 11 of 12 cases and the donor in 9 of 12. The founder-value multiple changed by −0.00286 on average. Four targets scored zero at every price; 8 had at least 2 different scores. Advice changed decisions, but the study does not show a reliable increase in business value.

**A stronger limit.** Even a perfect selector could beat keeping prices by only **0.00759** on these 12 targets. That is not enough for a 0.03 improvement against this free policy. This is a limit for this menu and these companies, not for all sales agents. The 48 calls cost [$0.003498624](results/market-handoff/meter.json). The simulation used local CPU only.

Score the sealed replies at no cost. No provider, Docker, simulator checkout or NumPy is required:

```sh
d=examples/seatlab/results/market-handoff
python3.12 -BS -m examples.seatlab.market_handoff.decisions score \
  "$d" "$d/sales-first.jsonl" "$d/finance-first.jsonl"
```

## Holdout: is the price menu worth another model campaign?

Protocol before scoring any donor company: keep the one price with the highest mean on the 12 targets. That price is option 1, unchanged. Use the separate 12 donors, with disjoint state fingerprints and matched market shapes. Run the same three 1,005-day continuations for each donor. No model is called. Do not replace a zero or a tie. Compare the mean best possible score with that frozen default on the 12 donors. If the gap is below 0.03, no agent with this menu can achieve the target improvement on this panel. If the gap is at least 0.03, the menu has room, not evidence that an agent can exploit it. Keep the original 12-pair test separate.

**Result: stop buying calls for this price menu.** Keeping prices earned **0.05272** on the 12 held-out companies. The best price, chosen with hindsight for each company, earned **0.05914**. The maximum possible gain was **0.00642**, below 0.03. Nine companies scored zero at all 3 prices. Keeping prices was optimal or tied for optimal in 10 of 12. The [receipt](results/market-handoff/ceiling.json) includes all 36 held-out outcomes and the input hashes.

```sh
python3.12 -BS -m examples.seatlab.market_handoff.ceiling examples/seatlab/results/market-handoff
```

## Run one business team in Harbor

You can stage any of the 12 companies in Peter's Olam fork. The sales seat reads the selected sales view and submits one recommendation. The finance seat must wait for it. Finance then sees its own view and the recommendation, but not the sales view. The verifier maps the finance choice to the frozen stock outcome. Neither seat receives the menu of outcomes. The same task can replay sealed choices without a model or ask 2 live DeepSeek seats. This tests one checkpoint decision, not a team operating a company for 3 years.

Start in the root of this branch. Use Python 3.12 for staging, the fork's Python environment for Harbor, and a running local Docker engine.

```sh
d=examples/seatlab/results/market-handoff
out=$(mktemp -d)
python3.12 -BS -m examples.seatlab.market_handoff.native_stage stage "$d" "$out" \
  --world 1 --arm donor --mode replay \
  --sales-replies "$d/sales-first.jsonl" --finance-replies "$d/finance-first.jsonl"
"$HARBOR_PYTHON" -B -m examples.seatlab.market_handoff.native_run "$out"
python3.12 -BS -m examples.seatlab.market_handoff.native_receipt "$out"
```

Set `HARBOR_PYTHON` to the absolute path of the Python executable in the fork's environment. On Peter's machine, run the middle command through `/tmp/crucible-docker-slot.sh`. Use a new output directory for each trial. The receipt checks both submitted views, prompts and choices, then matches the actual Harbor reward against the stock outcome. Missing evidence is an error; a measured reward of zero is not.

| Staged team | Replay: sales → finance | Replay reward | Fresh seats: sales → finance | Fresh reward |
|---|---:|---:|---:|---:|
| World 0, own sales view | 1 → 1 | 0.00000 | 3 → 3 | 0.00000 |
| World 1, donor sales view | 3 → 3 | 0.18797 | 1 → 1 | 0.25984 |

Both replays and both fresh teams completed in Harbor. [Four native receipts and their token costs](results/market-handoff/native.json) identify the trials. The fresh teams used 4 new calls and cost **$0.000122658**. The replay rows used no model calls. These 2 different worlds test transport and grading; they are not a paired estimate of the handoff effect.
