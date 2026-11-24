# Recover one shipment across two native seats

Use [Ship Handover](../tasks/ship-handover/README.md) to test whether a change of operator loses the identity of unfinished work. The outgoing seat knows the original request reference. The relief seat decides whether to dispatch. Neither seat knows whether the supplier already dispatched. Both seats run under test. The native reward counts parcels, not messages.

To keep the same named team and change when the supplier settles, use [the two-world operator](two-worlds.md). It accepts worker packets independently and reports what the missing workers can still change.

## Run one team

Use this fork with its locked dependencies and a working local Docker environment. From the checkout root:

```bash
export PYTHONDONTWRITEBYTECODE=1
uv run harbor trial start -c examples/ship_handover/sample-trial.yaml
```

The example uses 2 scripted native `oracle` participants. It uses no model. The outgoing seat offers the original reference. The relief seat reads it and resumes the request. This example uses a dropped request and a channel that delivers the reference. Do not count this standalone example as a registered result.

## Register a question and give each worker a shard

For example: **Does delivering the reference reduce omissions without increasing duplicate dispatches?** Set the team, block count, and pass rule before starting. Each block has one run in each combination of prior supplier state and channel. Blocks do not replay the same model decisions. The host knows the assignment. Do not mount the registration or launch config in the seat containers.

```bash
study=$(mktemp -d)
roster=examples/ship_handover/samples/control-team.json
uv run python -B -m examples.ship_handover.design --participants "$roster" \
  --team two-shifts --blocks 8 --out "$study/registration.json"
reg="$study/registration.json"
uv run python -B -m examples.ship_handover.study emit "$reg" --out "$study/w0" --shard 0/2
uv run python -B -m examples.ship_handover.study emit "$reg" --out "$study/w1" --shard 1/2
```

This creates 32 configs and launches nothing. For a model study, supply a file with the same `participants` object. Give each named native participant a `seat`, `name`, and `model_name`. Put credential references, never values, in participant `env`. Set provider limits outside the task. Use authorized credentials only. The benchmark does not require an account or a paid model to qualify its own controls.

Give each worker the same registration bytes and source revision. Assign disjoint shards on separate hosts. On a shared filesystem, workers can also take from the same worklist. The commands below show worker 0. Worker 1 uses `w1` and `1/2`.

```bash
python3 -BS -m examples.native_worklist status --study "$reg" "$study/w0" 0/2
while :; do
  if cfg=$(python3 -BS -m examples.native_worklist take --study "$reg" "$study/w0" 0/2); then
    uv run harbor trial start -c "$cfg" || exit 1
  else
    rc=$?; [ "$rc" -eq 3 ] && break; exit "$rc"
  fi
done
```

Use your site's concurrency limit around `harbor trial start`. A claim is permanent. It is not an attempt, a result, or a license to run a second copy. Keep the native failures. Do not automatically resubmit a held claim after a crash: the host might have submitted it. An independently copied worklist cannot coordinate with the original.

## Read the results without running a seat

The collector uses Python 3.12 or later and this checkout. It needs no Harbor installation, Docker, network, or key. It takes **workspace roots**, not the `trials` subdirectories. Give it stable copies after writers finish.

```bash
python3 -BS -m examples.ship_handover.collect "$reg" "$study/w0" "$study/w1"
python3 -BS -m examples.ship_handover.collect "$reg" "$study/w0" "$study/w1" --json
python3 -BS -m examples.ship_handover.contrast "$reg" "$study/w0" "$study/w1"
```

One row remains for every ticket in the original registration. A missing run, a partial run, a native error, an inconsistent record, or an ambiguous copy is not a grade of 0. A valid omission or duplicate **is** a grade of 0. The collector checks both registered seats, the task lock, the native grade, and the independent replay of the frozen supplier ledger. A copied native trial or world is not a second independent team.

You can restore the workspace on another machine. Keep `task.sha256`, `configs/`, and `trials/` together. The collector checks the recorded origin without opening paths from the source machine. Supply a matching trusted task checkout. These checks establish consistency, not who produced the records. Collect from the restored copy **instead of** the original. If you supply both, every shared ticket is ambiguous. Never use the restored copy to launch more trials.

Use `reference_before_resolve` to distinguish a reference returned before the decision from a late inbox read. A null value means no pre-decision inbox return. The string `none` means the inbox returned before the decision, but returned no reference. An accepted original reference does not prove attention, intent, or what the same team would have done in another world.

`contrast` keeps both supplier states separate. It reports the change in exactly-one dispatches for deliver minus withhold in each state. It then averages those two changes with equal weight. A missing grade can be 0 or 1. The bounds show every possible endpoint, not sampling uncertainty. A team without all four registered conditions has no overall contrast.

## What the native controls establish

We [registered 26 scripted first attempts](samples/control-registration.json) and [their predictions](samples/control-predictions.json) before starting either shard. All 26 finished with both roles under test. The [redacted results](samples/control-outcomes.json) matched **234 of 234 predicted fields**. There were 14 exactly-one outcomes, 9 omissions, and 3 duplicate dispatches. These counts qualify the controls. The control matrix does not estimate a population success rate.

| Outgoing policy → relief policy | Channel | Prior dispatch committed | Prior dispatch dropped |
|---|---|---|---|
| Original → resume received reference | Deliver | Exactly one; unchanged | Exactly one; first dispatch |
| Original → resume received reference | Withhold | Exactly one; unchanged | Omission |
| Draft → resume received reference | Deliver | Duplicate | Exactly one; first dispatch |
| Draft → resume received reference | Withhold | Exactly one; unchanged | Omission |
| Both seats idle | Deliver | Exactly one; unchanged | Omission |
| Original after relief closes → read late | Deliver | Exactly one; unchanged | Omission |

The draft control shows why one average is insufficient. Delivering the draft replaces an omission with a correct dispatch in one state. It replaces a correct dispatch with a duplicate in the other. The idle control shows why a positive team reward is not proof of recovery. The late control shows why an inbox return alone is not evidence available at decision time.

The original request settles **before** the seats start. This task does not model a supplier that completes later, expiring idempotency keys, crashes, or external side channels. The world gives neither seat the supplier's hidden state. Each run gets new opaque references. These controls do not measure how a model chooses which reference to send or whether to wait.

## Next model study

Register one named model team and 8 four-world blocks before launch. Keep the team, limits, and roster fixed. Use each ticket once. First ask whether delivery helps in at least one supplier state without hurting the other. Count that claim only if the two lower bounds are nonnegative and one is positive. Report the per-state counts even when the mean is zero. Report missing teams against the original registration. This is a decision about those registered trials, not proof that models in general benefit. The named-model study has not run.
