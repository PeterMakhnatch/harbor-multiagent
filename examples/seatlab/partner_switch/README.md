# What changes when the replacement partner actually speaks?

Use this benchmark when a partner's first action changes the information available for the second decision. A replay of old votes can answer the wrong question. This benchmark runs each candidate pair's reports through the native Joint Reopen world before asking either role to vote. It uses the task's existing verifier. It requires no Docker, API key or model call.

## Run the qualification first

```sh
switch_team() { python3 -BS -m examples.seatlab.partner_switch "$@"; }
switch_team qualify --start 9327 --control different
switch_team qualify --start 9327 --control same
```

**Question:** Does replaying original-partner votes overestimate how well a team coordinates with a replacement partner? **Pass test:** Both home pairs must score 1. With different conventions, mixed native pairs must score 1/2 and frozen-home votes must score 1. With the same convention both must score 1. The report distributions for each role must match across teams.

The 6 calendars have the same incumbent and 3 public alternative labels. Each role has 2 available alternatives. The calendars share exactly 1. Each role reports **one genuinely available slot**. It then votes after reading its actual partner's report. The verifier checks the sealed votes against the calendar. Its separate `report_true` flag means *full calendar equality*. A truthful one-slot report is incomplete by that definition; the flag is not this score.

The 2 reference teams encode the same information differently. Each knows the same public cycle of 3 labels. One team reports the next label after the one it cannot use. The other reports the previous label. An original partner can decode the sender's calendar. A replacement can infer the wrong calendar despite receiving a truthful message. This control does not give a seat the seed, shared slot, other calendar or partner identity.

The distinction follows [Lowe et al., *On the Pitfalls of Measuring Emergent Communication* (AAMAS 2019)](https://arxiv.org/abs/1903.05168). A sender's message can predict its own action without affecting its partner. This benchmark compares a receiver's actions under actual partner substitution. It does not implement the paper's general communication metric.

## Bring your own role agents

1. Register 2–6 teams. Keep the operator files outside the checkout.

```sh
work=$(mktemp -d)
switch_team begin --start 9327 --team alpha --team beta --out "$work/reports" > "$work/report-issued.json"
report_pin=$(python3 -c 'import json,sys;print(json.load(sys.stdin)["study_sha256"])' < "$work/report-issued.json")
mkdir -m 700 "$work/report-captures" "$work/vote-captures"
```

2. Give each role only its own prompts. The operator manifest lists the name, role, folder, deck pin and number of prompts. `t00-east` belongs to the first team's east role. The adapter iterates `requests.jsonl`. It sends only the `prompt` text and saves the first raw reply as `reply`, with the original `id` and `prompt_sha256`. Each reply is one JSON line. Both stages expect `{"slot":"A"}` with a legal label. The report must also belong to the role's own calendar. Do not give a seat the operator manifest, other prompts, grade or pairing identity.

For parallel collection, assign each operator one fixed worker number. Run this command with `--worker 0` or `--worker 1` for 2 operators. Repeat it for the vote stage using the vote plan, pin and capture directory.

```sh
switch_team status --plan "$work/reports" --pin "$report_pin" \
  --captures "$work/report-captures" --workers 2 --worker 0
```

`status` lists only that worker's roles but gives global totals. The assignment never shifts as other roles finish. `missing` means there is no published capture, not that a model was never called. `invalid` means the role output has failed validation. A report naming an unavailable slot is invalid, even if its JSON is valid. Exit `0` means all selected roles are complete; `1` means some are not; `2` means the study or command is invalid. Use different worker numbers. This is a read-only assignment, not a lock or automatic retry.

3. Freeze each role's replies in the matching capture folder. Use its deck pin, **not** the study pin. A complete role may finish independently of the other roles.

```sh
ballots() { python3 -BS -m examples.seatlab.convention_crossplay.ballots "$@"; }
east_deck_pin=$(python3 -c 'import json,sys; print(next(d["deck_sha256"] for d in json.load(sys.stdin)["decks"] if d["folder"]=="t00-east"))' < "$work/report-issued.json")
east_replies="$work/alpha-east-replies.jsonl"  # First replies from this role's adapter.
ballots freeze --deck "$work/reports/t00-east" --deck-sha256 "$east_deck_pin" \
  --source alpha --role east --replies "$east_replies" --out "$work/report-captures/t00-east"
```

4. After all report captures are complete, let the actual reports determine the next set of requests. Repeat steps 2–3 with the new decks and `vote-captures`. Do not reuse any report-stage replies, even if their JSON has the same shape.

```sh
switch_team advance --report-plan "$work/reports" --report-pin "$report_pin" \
  --report-captures "$work/report-captures" --out "$work/votes" > "$work/vote-issued.json"
vote_pin=$(python3 -c 'import json,sys;print(json.load(sys.stdin)["study_sha256"])' < "$work/vote-issued.json")
```

5. Score every original and mixed pair against the same 6 calendars.

```sh
switch_team score --report-plan "$work/reports" --report-pin "$report_pin" \
  --report-captures "$work/report-captures" --vote-plan "$work/votes" \
  --vote-pin "$vote_pin" --vote-captures "$work/vote-captures"
```

Use `--expect-overestimate positive|zero|negative` and `--require-matched-reports` to check a declared hypothesis. A missed result gate returns `1` **with** its panel. Incomplete or inconsistent evidence returns `2` **without** a panel. Never turn missing responses into wrong votes or drop incomplete teams without reporting them. The vote plan pins the exact report plan and captures. Changing a submitted report requires a new vote plan and fresh replies.

For a no-model adapter check, send the exact prompt to `python3 -BS -m examples.seatlab.partner_switch.respond --role east --convention forward` on standard input. It emits one ordinary slot reply. Use `reverse` for the opposite convention. Never pass this scripted answer to an experimental agent.

## Interpret the panel

Rows select the east participant. Columns select the west participant. `native` uses both votes after actual delivery. `frozen_home_vote` uses each role's original-partner vote for the same calendar and grades that pair on the same native branch. It is a diagnostic, **not** a second observed interaction. `mixed_overestimate` is frozen mixed reward minus native mixed reward. The panel also counts changed incoming reports and changed votes, and checks per-role report histograms.

One captured decision is reused for each exact role view. This keeps a changed partner from introducing new sampling noise on an unchanged view. It does not measure continuing private conversations, hidden model state, partner recognition or long-horizon adaptation. The input includes a role's own sent report at the vote. Use fresh complete cohorts to estimate stochastic variation. Matrix entries are not independent trials. A capture digest proves byte consistency, not model identity or truthful operator provenance.

Use [native partner trials](../partner_native/README.md) when each seat must act again with the actual replacement partner. The native study accepts ordinary Harbor participants and keeps registered but unobserved teams in its results.
