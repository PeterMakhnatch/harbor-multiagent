# Would this team still work with a different partner?

Use this before a paid team study. Some tasks reward a private convention that works only with the original partner. This check freezes each seat's rule, exchanges the partners and scores every pairing on the same physical decisions. No model, container, API key or Harbor install is required.

## Run it on Joint Reopen

From the fork checkout, use Python 3.12 or later. `work` must be outside any Git checkout. The two teams below use the same calendar task and information. Only their fallback choices differ.

```sh
cross() { python3.12 -BS -m examples.seatlab.convention_crossplay "$@"; }
work=$(mktemp -d)
cross native --start 26109 --sender truthful --arm together \
  --export "$work/teams" --expect-gap positive > "$work/baseline.json"
pin=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["design"]["export"]["roster_sha256"])' < "$work/baseline.json")
for arm in together independent apart; do
  cross score --start 26109 --sender truthful --arm "$arm" \
    --roster "$work/teams/roster.json" --roster-sha256 "$pin" --require-equal-home
done
```

Both seats have already read the other seat's report in the native world. The schedule controls whether each sees it in the final vote packet. “Together” means both keep the report or both lose it, with equal weight. “Apart” means exactly one keeps it. “Independent” gives each seat a separate fair draw. Each seat sees the report half the time in every schedule. The native verifier rewards only two correct final votes.

On the 6 development calendars selected from `26109`, fitting the truthful “together” control produces 2 distinct teams. Fit once, save both teams and change only the schedule:

| Report retention | Original partners | Mixed partners | Difference |
|---|---:|---:|---:|
| Together | 3/4 | 1/2 | 1/4 |
| Independent | 5/8 | 1/2 | 1/8 |
| Apart | 1/2 | 1/2 | 0 |

Do not confuse the “together” schedule with its both-retain half. When both actually retain the truthful report, original and mixed partners both score 1. When neither retains it, original partners score 1/2 and mixed partners score 0. The fallback decision accounts for the entire difference.

A context change can remove the need for a shared fallback without improving the mixed team's score. Do not read a smaller gap as a stronger team. Use `--sender pass` for the empty-report control. Fit it separately: a recorded empty report and a truthful report are different inputs. The empty-report original partners score 1/2 and the mixed partners score 0 in all 3 schedules. An empty report is not a control for the absence of a convention.

## Bring 2 to 12 teams

Use the [closed two-seat game format and role tables](../coupled_retention/README.md#test-a-different-two-seat-game). A role table maps the SHA-256 digest of each exact private observation to one legal action. Author each role from its own inputs. A new observation, an extra decision or an invalid action refuses the entire score. The finite game permits up to 256 weighted rows and 16 actions per role. Seats can have different action labels.

```sh
cross enroll --team first "$east1" "$west1" --team second "$east2" "$west2" \
  --out "$work/submitted" > "$work/enrolled.json"
pin=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["roster_sha256"])' < "$work/enrolled.json")
cross score --game "$game" --roster "$work/submitted/roster.json" \
  --roster-sha256 "$pin" --require-equal-home --expect-gap positive
```

Keep the returned roster pin separately. `enroll` writes a new private directory. It never replaces a roster. Its manifest pins the exact bytes for each role; `score` also verifies the manifest itself. Use trusted parent directories. A digest does not prove who wrote a rule, hide a short observation, or show that the roles developed independently. Do not give both tables or the score matrices to an experimental seat.

Read `closed_role_replay.matrix` as **row = east team, column = west team**. The diagonal contains the original partnerships. The rest contains all ordered mixed partnerships. First average the native reward across the weighted worlds for each pair. Then give each distinct partnership equal weight within its group. The output includes both denominators, the smallest and largest mixed score, and the digest of the ordered weighted game. Copies of an entire team cannot increase its weight; teams may share one role. Missing data is an error, not a zero. Exit `0` means the gates passed, `1` means a requested result did not hold, and `2` means the input or output was invalid. A missed gate still returns its full matrix.

## What the native control proves

The native command starts with one optimal pair of role-only rules. It applies all 6 permutations of the 3 active calendar labels, checking the weighted joint observations and **both** payoff axes for every permutation. It keeps all distinct resulting teams and records duplicate multiplicities. It does not search for the worst partner or enumerate every optimal strategy. The matrix includes no calls to model seats and no live Harbor trials.

The question comes from [Hu et al., *Other-Play* (ICML 2020)](https://proceedings.mlr.press/v119/hu20a.html): self-play partners can rely on incompatible conventions. This check does not implement their training algorithm. [Wolski et al. (2026)](https://arxiv.org/abs/2608.03644) distinguish crossing random seeds from crossing implementations. Their study found that inter-seed evaluation was a reasonable proxy for Other-Play in their setting. These label permutations are neither independent training seeds nor separate implementations. Use independently authored or collected rules before making a claim about external agents. Use live partners if either can change the other's later observations.

## Compare decisions from your own agents

Use this path if your evidence is a saved answer from each role, not a hand-written convention. No command below starts an agent. Prepare a **new deck for each team and role**. Use a private directory outside the checkout.

```sh
ballots() { python3 -BS -m examples.seatlab.convention_crossplay.ballots "$@"; }
work=$(mktemp -d)
ballots prepare --start 9327 --sender truthful --role east --out "$work/a-east" > "$work/a-east.json"
pin=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["deck_sha256"])' < "$work/a-east.json")
```

Each line of `a-east/requests.jsonl` contains `id`, `prompt_sha256`, and `prompt`. The seat sees **only the exact prompt**, not the ID, hash, other prompts, partner, filenames, or grade. For an authored game, use `--game PATH` instead of `--start` and `--sender`. Authored observations must decode as UTF-8. State the response contract before collection: native packets request `{"slot":"A"}`; authored games accept `{"action":"LABEL"}`. Put the role's legal action labels and instructions in the authored observation itself. Do not add unrecorded hints.

An adapter saves one JSON line per first reply with the same `id`, the same `prompt_sha256`, and the **unmodified reply text** in `reply`. Never retry a bad decision or select the best of several replies. Keep refusals and invalid answers. Keep different roles in separate sessions with no access to each other's files. Save replies outside the checkout. Freeze them before grading.

```sh
ballots freeze --deck "$work/a-east" --deck-sha256 "$pin" --role east \
  --source alpha --replies "$work/a-east-replies.jsonl" --out "$work/a-east-frozen"
```

Repeat `prepare` and `freeze` separately for west on `alpha`, and for both roles on `beta`. Multiple reply files may arrive in any order. A missing or ambiguous answer produces a coverage report and no strategy file. Valid but wrong actions stay in the data. Do not treat a missing answer as a zero team reward. Do not silently drop incomplete teams from a study: report all registered teams and their completion counts.

```sh
ballots compare --start 9327 --sender truthful --arm together \
  --team alpha "$work/a-east-frozen" "$work/a-west-frozen" \
  --team beta "$work/b-east-frozen" "$work/b-west-frozen" > "$work/first.json"
cohort=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["cohort"]["cohort_sha256"])' < "$work/first.json")
ballots compare --start 9327 --sender truthful --arm apart --cohort-sha256 "$cohort" \
  --team alpha "$work/a-east-frozen" "$work/a-west-frozen" \
  --team beta "$work/b-east-frozen" "$work/b-west-frozen"
```

`compare` counts **registered teams**, not distinct conventions. Two teams that choose the same actions still count twice if they have separate decks and receipts. A copied deck cannot count twice. The output reports both team and distinct-behavior counts. Keep the cohort digest separately to fix the evidence and order across conditions. The digest proves file consistency, not who answered or whether this was truly their first attempt.

A frozen answer is reused wherever that exact observation recurs. Matrix entries are not independent trials. For a stochastic agent, capture independent complete cohorts if you need to estimate variation. These recordings cannot prove live adaptation to a new partner. Existing Joint Reopen action logs lack the exact projected prompt and first reply. An unrelated `{"pick":2}` response is not a ballot. Neither is a valid substitute for a fresh capture.

If a replacement changes what the next seat sees, use the [two-stage partner switch](../partner_switch/README.md). It collects the senders' reports first. It then asks for votes only after the native world delivers the actual partner's report.
