# Can identical individual accuracy hide different team performance?

Use this benchmark to test a team-memory or private-context design before paying for model runs. Two teammates have already exchanged their updated calendars. Both must now cast sealed votes. We change whether they lose the other calendar **together or separately**. Every individual receives the same distribution of actual input bytes in each condition. Only the pairing of those inputs changes.

This differs from asking whether a single receiver retained a useful correction. Here both votes are necessary. A transcript reader who merges both private views can appear perfect even when no pair of separate receivers can match it.

## Qualify the benchmark

From this fork's root, with Python 3.12 or later:

```sh
python3.12 -BS -m examples.seatlab.coupled_retention qualify \
  --start 0 --sender truthful --expect-team-gain yes
python3.12 -BS -m examples.seatlab.coupled_retention qualify \
  --start 0 --sender pass --expect-team-gain no
```

Before running a participant, require that the roles' packet distributions and individual accuracy match, but that truthful notes yield a positive team gap. The gap must disappear when both senders pass. Exit 0 means the requested expectation holds; 1 means it fails; 2 means the input is invalid or outside the solvable range. These commands start no models or containers.

### The native decision

The adapter uses the unmodified [Joint Reopen task](../../tasks/joint-reopen/README.md). Both roles endorse the same incumbent meeting. Both then learn they cannot attend that meeting. Each role has two different alternatives. Exactly one alternative works for both. Both roles send their actual slots, or both explicitly pass. Both request their inbox **before either votes**. The adapter retains public information and the receiver's own card. It keeps or omits the returned peer note. Two `?` marks mean the note was omitted. Two `-` marks mean the receiver retained an empty report. The packets are a controlled projection of the native exchange, not full live prompts. No reread or further message is allowed.

The adapter finds six ordinary development seeds with one incumbent and one set of three alternative labels. It covers every ordering of the shared, east-only and west-only alternatives. It evaluates all 36 legal vote pairs in each native world. The independent native verifier provides the physical payoff. The team earns 1 only if both select the correct common slot. The diagnostic individual accuracy is agreement with that same slot, not a separate native reward. Do not treat six seeds with different slot labels as six different tasks.

### What is held fixed

- **Together:** half the time both receivers retain the note; otherwise neither does.
- **Apart:** exactly one receiver retains the note. Each role gets it half the time.
- **Independent:** the four possible pairs of retained and omitted notes have equal weight.

Each arm has 24 equally weighted designed rows, not 24 independent teams. They represent the same six worlds. Within each world and for each role, the exact packet occurs with the same weight in every arm. The public receiver picks the shared slot if its notes identify one. Otherwise it picks the first slot on its own sorted card. Both seats use the same rule. The reader does not get a seed, arm, score table, or another seat's vote.

`reference.team` is the public rule's physical reward. `reference.east` and `reference.west` are individual accuracy. `split` counts different final votes. `agree_wrong` counts identical votes for the wrong slot. `ceilings.decentralized` is the best pre-agreed pair of rules that each read only their own packet. Those rules may differ by role. `pooled` instead lets one operator see both packets before choosing both votes. `oracle` also sees the world. These are different information budgets, not competing models.

### Scripted control results

| Truthful report retention | Each seat correct | Team correct | Best separate rules | Both packets pooled |
|---|---:|---:|---:|---:|
| Together | 3/4 | 2/3 | 3/4 | 1 |
| Independent | 3/4 | 7/12 | 5/8 | 1 |
| Apart | 3/4 | 1/2 | 1/2 | 1 |

Identical individual accuracy hides a **1/6 difference in native team reward**. This is not just the scripted receiver's choice of a fallback. The best separate rules also differ between together and apart. With explicit passes, the public rule's team reward is 1/3 in all three arms; the best separate rules get 1/2. A pooled reader can still get 1 by intersecting the two private calendars. That reader is not a valid baseline for a pair of independent seats.

### Give two independent receivers a case

Keep the commands, seeds and operator files out of the participants' contexts. Give each participant only their own packet. This example uses two fresh Python processes as scripted receivers. It does not isolate hostile code. Do not host an untrusted participant in your own checkout.

```sh
set -eu
umask 077
work=$(mktemp -d)
game() { python3.12 -BS -m examples.seatlab.coupled_retention "$@"; }
receiver=examples/seatlab/coupled_retention/policy.py
for role in east west; do
  game packet --start 0 --case 0 --sender truthful --role "$role" --note retained > "$work/$role.packet"
  python3.12 -IS "$receiver" < "$work/$role.packet" > "$work/$role.vote"
done
game grade --start 0 --case 0 --sender truthful \
  --east-vote "$work/east.vote" --west-vote "$work/west.vote"
```

A valid commit is one JSON object such as `{"slot":"A"}`. An absent or malformed vote is not a valid zero. Preserve both decisions separately. The grade does not authenticate the receiver or the source of its vote. For open-ended chat, new calendar requests or actual model behavior, use the native task with its own isolation and record a different experiment.

## Test a different two-seat game

`inspect` uses no task-specific imports to calculate the values of your supplied private observations and joint payoffs. Give it a JSON file with `schema` set to `seatlab.two-seat-game/v1`, `east_actions` and `west_actions` as ordered lists of unique action labels, and `rows` as a list. Each row contains a positive integer `weight`, canonical base64 observations `east_b64` and `west_b64`, and a binary `scores` matrix. East chooses a row; west chooses a column. Neither sees the partner's observation. Use stable role inputs, the true legal actions and the native grader, not an aggregate transcript. The checker rejects games that exceed its enumeration limit instead of reporting an approximation.

```sh
python3.12 -BS -m examples.seatlab.coupled_retention inspect game.json --expect-pooling-gap yes
```

The [Bespoke Seed to Profit trace](https://benchmarks.bespokelabs.ai/multi-agent-coordination-bench/#task-seed-to-profit) describes participants issuing incompatible final assignments. That motivates checking the pair of commitments. It does not establish a memory failure or validate this task as a proxy for that benchmark. No result here establishes how a frontier model would act.
