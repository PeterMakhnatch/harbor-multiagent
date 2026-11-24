# Operate a two-seat study without a shared worker disk

Use one admission ledger for both shipping tasks. Workers can use different directories. A worker may run a native trial only after a fresh grant. A lost grant is a missing outcome, not a reason to start again. The ledger gives at most one permission per ticket. It cannot stop a launcher that ignores the protocol.

## Inspect the released campaign

Run this from the fork checkout. It needs Python 3.12 or later. It does not need Docker, Harbor, provider keys or private trial files.

```sh
p=examples/ship_handover/samples/disconnected-workers
d=sha256:334a29d45edb19bf10f6088cf8df90e4b5464b8e87ffe794de847613c8697787
python3.12 -BS -m examples.ship_handover.campaign_report "$p/stock.json" "$p/inflight.json" "$p/plan.json" "$p/status.json" \
  --expected-plan "$d" --stock-packet "$p/stock-w0.json" --inflight-packet "$p/inflight-w0.json" \
  --stock-packet "$p/stock-w1.json" --inflight-packet "$p/inflight-w1.json"
```

The ledger issued all 8 tickets. Only 7 native teams started. All 14 seats returned. Four teams earned 1; three earned 0. Each known reward agreed with an independent replay of the supplier ledger. The lost ticket still has no grade. The team used scripted native `oracle` seats. No model ran. Cost: $0.

| Test | Observed result |
|---|---|
| Copy a staged directory byte for byte | The worker refused the foreign trial path before it called the authority. |
| Stage the same assignment in two fresh directories and race their first ticket | One worker got a fresh grant. The other got `already_issued`. One native trial started for that ticket. |
| Discard a different grant after the coordinator writes it | The worker got no config. A newly staged worker could not recover the grant. Nothing started for that ticket. |
| Compare the final and initial ledger snapshots against the same 7 grades | The initial snapshot listed all 7 as unissued. Both snapshots kept the same grades and effect bounds. |

This last case matters. An unissued ticket with a grade shows disagreement between records. A stale status snapshot is sufficient to create it. The records alone do not prove an unapproved start.

The sender offered a draft. When it arrived, relief resumed the draft: that made a duplicate if the original succeeded and one parcel if it failed. Withholding helped on a successful original in both tasks. For failed originals the *difference between the two delivery effects* lay between 0 and +1. Do not impute the scheduled action for the missing attempt. See the [registered question](samples/disconnected-workers/question.json) and [qualified native rows](samples/disconnected-workers/qualification.json).

## Start a campaign

1. Register both tasks with the same named roster. Use [the two-world guide](two-worlds.md). Fix a spending limit and pass rule before starting model seats. Put your private manifests in `$work/stock.json` and `$work/inflight.json`. Do not mount them in the seats. Use the same fork checkout at each site.

2. On the coordinator, create a plan and its **only** live authority. Use a new private directory on a durable local disk. A restored or duplicated live database can issue a ticket twice. Do not use a network filesystem or a replica. The `init` command creates a new ledger directory.

```sh
umask 077
work=$(mktemp -d)  # For this scripted example only. Use durable storage for a real campaign.
p=examples/ship_handover/samples/disconnected-workers
cp "$p/stock.json" "$p/inflight.json" "$work/"
python3.12 -BS -m examples.ship_handover.campaign_plan "$work/stock.json" "$work/inflight.json" \
  --workers 2 --out "$work/plan.json" > "$work/plan-receipt.json"
d=$(python3.12 -BS -c 'import json,sys; print(json.load(open(sys.argv[1]))["plan"])' "$work/plan-receipt.json")
python3.12 -BS -m examples.seat_seals.admission init "$work/plan.json" "$work/ledger" --expected-plan "$d"
```

3. Give each trusted worker its index, both registrations, the plan, and the digest. Give it one absolute `--via` executable that forwards stdin to the coordinator and returns the reply on stdout. The [admission guide](../seat_seals/admission.md) gives local and SSH examples. Only the coordinator may open the ledger. The wire does not authenticate the worker. Protect access at the site. This release tested two logical workers on one Mac. It did not test SSH or independent physical hosts.

4. At each worker, use the installed fork runtime to stage a **new absolute directory**. Use `i=0` or `i=1`. Both assignments include native stock and in-flight trials. Do not relocate the staged directories: their configs contain absolute paths.

```sh
i=0
root="$work/worker-$i"
uv run python -B -m examples.ship_handover.campaign_worker stage "$work/stock.json" "$work/inflight.json" \
  "$work/plan.json" "$root" --worker "$i" --expected-plan "$d"
```

5. Ask for one config. Set `via` to your trusted transport executable. Start the returned config only on exit 0. Apply your normal site concurrency and spending limits. `take` never calls Harbor and never runs a seat.

```sh
if cfg=$(python3.12 -BS -m examples.ship_handover.campaign_worker take "$work/stock.json" "$work/inflight.json" \
    "$work/plan.json" "$root" --worker "$i" --expected-plan "$d" --via "$via"); then
  uv run harbor trial start -c "$cfg"
else
  rc=$?
  printf 'No start: take exited %s\n' "$rc" >&2
fi
```

Exit 3 means the local assignment is exhausted. Exit 4 means the authority already issued that ticket. You may select the next ticket on the same root. Exit 2 means bad input, a storage failure, or an uncertain reply. Never start a trial from a saved or guessed config after an uncertain reply. A fresh worker must stage its assignment again and use the same authority. No operator should restore the authority to recover a lost ticket.

6. Stop the writers. Export the two worker packets with `examples.ship_handover.transfer_snapshot`, as in [the two-world guide](two-worlds.md). In this layout, the native workspaces are `$root/stock` and `$root/inflight`; the in-flight task is `$root/ship-inflight`. On the coordinator, save `python3.12 -BS -m examples.seat_seals.admission status "$work/ledger" --json`. Give the recipient that status, the registrations, the plan and the available packets. An absent worker is an unknown contribution, never a zero.
