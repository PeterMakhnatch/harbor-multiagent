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

You can stage any of the 12 companies in Peter's Olam fork. Sales submits one recommendation. Finance must wait for it. Finance sees its own view and the recommendation, but not the sales view. Neither seat receives the payoff table. The verifier maps the finance choice to the frozen stock outcome. Use recorded choices, live DeepSeek seats, or [your own trusted role programs](#bring-your-own-live-seat-programs). This tests one checkpoint decision, not a team operating a company for 3 years.

Start in the root of this branch. Use Python 3.12 for staging, the fork's Python environment for Harbor, and a running local Docker engine.

```sh
d=examples/seatlab/results/market-handoff
out=$(mktemp -d)
python3.12 -BS -m examples.seatlab.market_handoff.native_stage stage "$d" "$out" \
  --world 1 --arm donor --mode replay \
  --sales-replies "$d/sales-first.jsonl" --finance-replies "$d/finance-first.jsonl"
"$SLOT_WRAPPER" "$HARBOR_PYTHON" -B -m examples.seatlab.market_handoff.native_run "$out"
python3.12 -BS -m examples.seatlab.market_handoff.native_receipt "$out"
```

Set `HARBOR_PYTHON` to the absolute Python path in the fork's environment. Set `SLOT_WRAPPER` to the local Docker trial gate; on Peter's machine it is `/tmp/crucible-docker-slot.sh`. Use a new output directory for each trial. The receipt checks both submitted views, prompts and choices, then matches the Harbor reward against the stock outcome. Missing evidence is an error; a measured reward of zero is not.

| Staged team | Replay: sales → finance | Replay reward | Fresh seats: sales → finance | Fresh reward |
|---|---:|---:|---:|---:|
| World 0, own sales view | 1 → 1 | 0.00000 | 3 → 3 | 0.00000 |
| World 1, donor sales view | 3 → 3 | 0.18797 | 1 → 1 | 0.25984 |

Both replays and both fresh teams completed in Harbor. [Four native receipts and their token costs](results/market-handoff/native.json) identify the trials. The fresh teams used 4 new calls and cost **$0.000122658**. The replay rows used no model calls. These 2 different worlds test transport and grading; they are not a paired estimate of the handoff effect.

## Can the 12-company offline comparison survive native delivery?

**Question and arms, registered before the batch.** Replay the recorded sales and finance decisions through the actual two-seat Harbor task. On each of the 12 targets, run both the target's own sales view and its matched donor view. Use the two published, complete replay receipts above and start the other 22 local Docker trials. No new model may answer. This measures message delivery and grading, not agent resampling.

**Pass/fail.** Require exactly 24 unique native trial IDs, one per registered target and arm. Require both seats to complete, with no served model. Match both native picks and both user-prompt digests to the corresponding original direct decisions. Match the target prefix, selected stock outcome and actual native reward to the archived direct comparison. Recompute the paired mean difference and win/loss/tie counts. All 48 seat bindings and all 24 rewards must agree. A missing or failed trial is incomplete, not a zero; do not publish a matched mean for an incomplete panel. Report any mismatch without altering the original grades.

The 2 role services enforce the order of delivery. The native verifier reads an already computed stock payoff table; it does not start 3 years of new simulated trading. A pass confirms the replay transport, not the benefit of this sales policy.

**Result: all 24 native replays matched.** Two workers ran 11 new trials each through the shared Docker gate. We reused the 2 registered replay receipts; we did not reuse the 2 live teams as replays. All 48 role inputs and choices matched, and all 24 native rewards matched. With the target company's sales advice, the mean was **0.09468**. With the donor's advice, it was **0.09013**. The recorded team decisions gave 3 wins, 1 loss and 8 ties. The mean difference was identical to the original direct result. This batch used **0 new model calls**.

Verify the [native receipts](results/market-handoff/native-panel.json) and compare with the [published score](results/market-handoff/native-panel-score.json). This command needs neither Docker nor a model key:

```sh
d=examples/seatlab/results/market-handoff
m=examples.seatlab.market_handoff
python3.12 -BS -m "$m.native_panel" score "$d" "$d/native-panel.json" |
  cmp - "$d/native-panel-score.json"
```

To build a new batch, start with an empty private campaign directory. Give each worker the same directory and a different shard. These workers reserve a case before staging it. They do not automatically repeat an incomplete trial.

```sh
campaign=$(mktemp -d)
pids=""
for i in 0 1; do
  python3.12 -BS -m "$m.native_panel_work" "$d" "$campaign" \
    --harbor-python "$HARBOR_PYTHON" --slot-wrapper "$SLOT_WRAPPER" \
    --shard "$i/2" > "$campaign/worker-$i.json" &
  pids="$pids $!"
done
for pid in $pids; do wait "$pid" || exit 1; done
python3.12 -BS -m "$m.native_panel" collect "$d" "$campaign" > "$campaign/bundle.json"
python3.12 -BS -m "$m.native_panel" score "$d" "$campaign/bundle.json"
```

For the published campaign, a third worker examined both shards after completion. It recognized all 22 existing trials, reused the 2 published receipts and started **0** trials. New batches get new native trial IDs; compare the grade and seat agreement, not the UUIDs. A partial batch reports the missing cases and withholds the paired mean.

## Can an operator bring new sales and finance seats?

**Acceptance test, fixed before running outside policies.** First, two separate programs answer the published role prompts with the fixed keep-price policy. The programs get prompt packets only. Prepare the finance packets only after the sales replies arrive. On world 1 with the company's own sales view, require both first picks to be 1. Require both native prompt hashes to match the actual outside requests, and the native reward to match the offline stock grade. This is not a replication with new model draws.

Next, give a native trial two operator-supplied programs. In worlds 1 and 2, keep the inputs and the finance program fixed. In one arm, sales recommends keeping prices (1). In the other, it recommends discounting (3). Finance must return the recommendation from its *delivered* prompt. No answer or precomputed finance pick may be present in the native configuration. Pass only if all 4 trials finish, each role sees the expected prompt, the 2 finance prompts per world differ, and every reward equals the stock payoff for its delivered choice. Report even equal payoffs. Refuse invalid replies rather than choosing a default. These programs use no model; they test live orchestration, not team quality.

**Result: both delivery tests passed.** Two independent processes returned 24 sales decisions and then 24 finance decisions from prompt packets alone. The native replay of world 1 used their actual requests and choices. Both chose 1; the native verifier returned **0.25984**.

In 4 separate native trials, sales and finance ran as programs while the trial was active. The sales input stayed identical across arms in each world. The changed recommendation reached finance each time. All 8 role inputs and choices matched an independent reconstruction.

| Business | Keep: sales → finance | Native reward | Discount: sales → finance | Native reward |
|---|---:|---:|---:|---:|
| World 1 | 1 → 1 | 0.25984 | 3 → 3 | 0.18797 |
| World 2 | 1 → 1 | 0.00000 | 3 → 3 | 0.00000 |

The [5-trial evidence](results/market-handoff/external-seat-proof.json) includes trial IDs, both input digests per trial, and actual verifier rewards. A real change of decision did not require a change of reward. These were fixed policies, not 5 fresh LLM teams.

### Bring your own packet workers

Start at the fork root with Python 3.12. Keep the payoff files on the owner's machine. Give a sales worker only `sales.requests`. Give a finance worker only `finance.requests`, after sales returns. The included [reference worker](market_handoff/reference_seat.py) uses no model or network.

```sh
set -eu
m=examples.seatlab.market_handoff
d="$PWD/examples/seatlab/results/market-handoff"
policy="$PWD/examples/seatlab/market_handoff/reference_seat.py"
w=$(mktemp -d)
mkdir "$w/prefixes"
cp "$d"/*.prefix.json "$w/prefixes/"
python3.12 -BS -m "$m.decisions" emit-sales "$w/prefixes" > "$w/sales.requests"
python3.12 -IS "$policy" packet keep < "$w/sales.requests" > "$w/sales.replies"
python3.12 -BS -m "$m.decisions" emit-finance "$w/prefixes" "$w/sales.replies" > "$w/finance.requests"
python3.12 -IS "$policy" packet keep < "$w/finance.requests" > "$w/finance.replies"
python3.12 -BS -m "$m.decisions" score "$d" "$w/sales.replies" "$w/finance.replies"
```

For native confirmation, set `HARBOR_PYTHON` and `SLOT_WRAPPER` as [above](#run-one-business-team-in-harbor). The comparison accepts your own first replies, not only the published model's decisions.

```sh
mkdir "$w/replay"
python3.12 -BS -m "$m.native_stage" stage "$d" "$w/replay" --world 1 --arm own --mode replay \
  --sales-replies "$w/sales.replies" --finance-replies "$w/finance.replies"
"$SLOT_WRAPPER" "$HARBOR_PYTHON" -B -m "$m.native_run" "$w/replay"
python3.12 -BS -m "$m.delivery" "$d" "$w/replay" \
  --sales-replies "$w/sales.replies" --finance-replies "$w/finance.replies"
```

`delivery` returns 0 for a match, 1 for a measured mismatch, and 2 for invalid or missing input. It binds the two prompt hashes, view hashes, choices and verifier reward. It reports the supplied dataset hash, not proof of who produced a private dataset.

### Bring your own live seat programs

Use only programs you trust. The programs run on the host as your user, with separate temporary working directories. This is **not** an operating-system sandbox. A program can read same-user files or use the network. Harbor cannot meter an arbitrary program's model bill. Keep the command file private; the staged task contains no program paths, keys, or preselected actions.

Reuse `d`, `m`, `w` and `policy` from the packet example. This example requires `jq`. Finance reads the recommendation from its actual turn; it has no configured choice.

```sh
jq -n --arg py "$(command -v python3.12)" --arg policy "$policy" \
  '{sales:{argv:[$py,"-IS",$policy,"turn","keep"],env:[]},
    finance:{argv:[$py,"-IS",$policy,"turn","follow-sales"],env:[]}}' > "$w/commands.json"
mkdir "$w/live"
python3.12 -BS -m "$m.native_stage" stage "$d" "$w/live" --world 1 --arm own --mode command
"$SLOT_WRAPPER" "$HARBOR_PYTHON" -B -m "$m.native_run" "$w/live" --seat-commands "$w/commands.json"
python3.12 -BS -m "$m.delivery" "$d" "$w/live" \
  --sales-replies "$w/sales.replies" --finance-replies "$w/finance.replies"
```

Replace each `argv` with your own absolute executable and its arguments. A live program receives one JSON line with `schema: "seatlab.seat-turn/v1"` and `prompt`. It returns one JSON line, for example `{"text":"{\"pick\":1}"}`. `env` names host variables to pass to that role only. Never put secret values in `argv`. A failed process or an invalid reply does not receive a default decision.

The runner now rejects an invalid command file before it creates trial state. In the same staged world, we rejected a malformed configuration, supplied the valid commands, and completed native trial `115d041e-2aba-486d-87c7-d07ac7dce497`. Both the stock scorer and Harbor returned `0.2598390734632193`. This was a fixed-program diagnostic, not another model sample.

## Can another operator reproduce the native outcomes without the original trial directories?

**Registered before the new runs.** From an anonymous, shallow HTTPS checkout, stage and run all 12 companies in both arms. Start 24 new native trials. Include the 2 published anchor trials as new executions, not as reused receipts. Use only the released first replies from sales and finance. Do not start models. A grader run uses the published payoff table, not a fresh 3-year company simulation.

**Pass/fail.** Require 24 native IDs that differ from each other and from every original. For all 48 seats, match the delivered prompts and the choices. Also require all 24 native rewards, company prefixes and selected stock outcomes to match. Withhold the full-panel conclusion if an arm is absent or if one comparison fails. Keep every first attempt. Separately, require the workstation's portable gate to admit business and calendar workers without changing their interfaces. Preserve the site's existing 2-trial cap on every Docker command.

**Separate clean-environment check.** Install the fork's locked dependencies into a new private Python environment. Use it to execute world 0, own sales view, once more. Require the original recorded choices, a new native ID and the real stock grade, including a legitimate zero. Then give a fresh four-seat calendar (1255, certified-stop arm) to the supported signed-in reporters and local booker. Require a native grade and independent owner evidence for each reporter actually requested. Keep missing or wrong first outcomes. This checks portability across benchmarks, not a model or policy effect. Do not use API keys or paid fallback.

### Run or verify a separate repetition

To verify the **original** business study offline, use `python3.12 -BS -m examples.seatlab.workbench verify native-business-replay`. This re-scores published receipts; it does not start teams.

To collect **new native receipts**, complete the [workbench's native setup](../../README.md#seatlab-workbench). Run the following from the checkout root. Use a new, empty private directory. Give both operators the same value of `campaign`:

```sh
d=examples/seatlab/results/market-handoff
m=examples.seatlab.market_handoff
campaign=$(cd "$(mktemp -d)" && pwd -P)
```

Give the first operator `shard=0/2` and the second `shard=1/2`. Each runs this command once:

```sh
python3.12 -BS -m "$m.native_panel_work" "$d" "$campaign" \
  --shard "$shard" --include-anchors \
  --harbor-python "$HARBOR_PYTHON" --slot-wrapper "$SLOT_WRAPPER"
```

Without `--include-anchors`, the worker uses the 2 original receipts. With it, all 24 company/arm pairs require new native executions. Rerunning the command recognizes completed trials; it does not issue them again. It preserves incomplete first attempts. No model call is needed.

Check one fresh trial against its original:

```sh
python3.12 -BS -m "$m.native_repeat" "$d" "$d/native-panel.json" \
  "$campaign/market-00-own-replay"
```

An original trial cannot verify as its own repetition. The tool checks the two delivered views and prompts, the two choices and the grade. It expects a different native ID. It does not require the paths, raw configuration bytes or timestamps to stay the same.

After both workers finish, collect and score their fresh trial directories:

```sh
python3.12 -BS -m "$m.native_repeat_panel" collect "$d" "$d/native-panel.json" \
  "$campaign"/market-*-replay > "$campaign/new-receipts.json"
python3.12 -BS -m "$m.native_repeat_panel" score "$d" "$d/native-panel.json" \
  "$campaign/new-receipts.json"
```

The scorer withholds the paired result if a company or arm is missing, duplicated or wrong. It rejects an original receipt presented as a fresh one. It reports prompt agreement separately from reward agreement. Redacted receipts let readers check the claims against the released decisions. They do not authenticate a remote runner. An owner can verify the underlying native files with `collect`.

**Full-panel result.** The 24 new native IDs were distinct from each other and from all 24 originals. All 48 delivered role prompts and choices matched. All 24 stock rewards matched, including 11 actual zeros. The recorded decisions still gave 3 wins, 1 loss and 8 ties across 12 companies; the mean own-minus-donor difference was 0.00455. The two business workers shared local capacity with a four-seat calendar. Native timestamps showed at most 2 active trials. This is an independent execution of the same sealed decisions, not 24 fresh model samples.

Verify the [fresh receipts](results/market-handoff/native-repeat-receipts.json) against the [published report](results/market-handoff/native-repeat-score.json) without Docker or a model account:

```sh
python3.12 -BS -m examples.seatlab.workbench verify native-business-repeat
```

**Clean environment: one reproduced grade and one unresolved live turn.** A private install with uv 0.13.5 ran the recorded business team. Sales and finance both chose 1; stock reward was 0. A second operator used `--include-anchors`, reproduced the same choices with another new ID, then re-entered without starting a duplicate. The four-seat typed calendar on the shared gate also finished and scored 1.

The signed-in calendar on the private installation did **not** pass the separate owner-evidence check. One reporter accepted its request but returned no verified reply. The booking seat timed out; Harbor recorded no submission and a grade of 0. No client receipt establishes how many tokens that turn used. The worker kept the first attempt. The [redacted operator record](results/market-handoff/independent-operator.json) distinguishes this measured grade from the unknown usage. It does not establish what caused the missing reply.

**Separate operator acceptance, before launch.** Can both shell commands above complete the entire panel in the private, locked Harbor environment? Start a new campaign from public revision `d8498afbbc42007fa7566b30407d3aabbfbaca3b`. Require each shard to finish 12 first attempts and reuse none. Require all 24 IDs to be new relative to both published panels. Require all delivered choices and stock grades to match. Use no model calls and retain failures. This checks the public workflow; it adds no independent model decisions.
