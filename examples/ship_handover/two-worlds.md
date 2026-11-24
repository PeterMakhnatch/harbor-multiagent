# Run one team in two shipping worlds

Use this workflow when a recovery policy succeeds before a handover and may fail when the supplier has not finished. Both seats run under test. Use the same native agent configuration for each role. Give each trial its own ticket. No command below sends a prompt unless you explicitly start a model trial.

For workers without a shared disk, use [one coordinator across both worlds](disconnected-workers.md). It reports a lost start permission separately from a failed team.

## Read the released result

From this fork checkout, with Python 3.12 or later:

```sh
p=examples/ship_handover/samples/two-worlds
t=recovery
python3.12 -BS -m examples.ship_handover.transfer "$p/stock-$t.json" "$p/inflight-$t.json" \
  --stock-packet "$p/stock-$t-w0.json" --stock-packet "$p/stock-$t-w1.json" \
  --inflight-packet "$p/inflight-$t-w0.json" --inflight-packet "$p/inflight-$t-w1.json"
```

Change `t` to `sender-only` to inspect the same study with a silent relief seat. Omit both `w1` packets to see what worker 0 alone establishes. With no packets, the 8 registered tickets stay enrolled. Each difference between delivery effects then ranges from −2 to +2. These are exact bounds for missing grades, not confidence intervals.

| Original request | Stock: delivery benefit | In flight: delivery benefit | Change, in flight minus stock |
|---|---:|---:|---:|
| Original fails; both seats follow the recovery policy | +1 | 0 | −1 |
| Original succeeds; both seats follow the recovery policy | 0 | 0 | 0 |
| Either state; relief is the native `nop` seat | 0 | 0 | 0 |

Each entry compares delivery against withholding within one state and roster. One registered ticket supplied each cell. All 16 [registered first attempts](samples/two-worlds/question.json) matched their specified seat, receipt and physical outcomes. Both seats returned in each native trial. The [host qualification](samples/two-worlds/native-qualification.json) checked the native result independently against the frozen ledger. Two worker processes used separate directories on one Mac and one Docker engine. This was not a remote-host test.

The recovery seat got the original reference before its decision in both delivered failure cases. It resumed the request in both. In stock, resumption replaced a lost dispatch. In flight, resumption only joined the original request, which later failed. A sender with a silent relief seat could still earn 1 from the supplier. A successful dispatch is not evidence of cooperation. No language model ran in these 16 trials. Cost: $0.

## Register your own roster

Use the locked fork environment and local Docker for native trials. Keep the private roster, plan, configs and receipts outside the seat containers. Replace `roster` with the enrolled native roles for a model study. Fix the sample size, spending limit and pass rule before starting. These commands only materialize and register:

```sh
umask 077
work=$(mktemp -d)
roster=examples/ship_handover/samples/control-team.json
python3.12 -BS -m examples.ship_inflight.materialize "$work/ship-inflight" > "$work/task.json"
uv run python -B -m examples.ship_handover.design --world stock --team crew \
  --participants "$roster" --blocks 1 --out "$work/stock.json"
uv run python -B -m examples.ship_handover.design --world inflight --team crew \
  --participants "$roster" --blocks 1 --task "$work/ship-inflight" --out "$work/inflight.json"
```

For a larger model study, increase `--blocks` equally. A block fixes 2 states × 2 channels for each task. The two tasks must have the same label and native participant configs by seat. Equal labels alone do not establish the same roster. The workbench accepted and emitted 4 configs for a named `opencode` pair without credentials. None started. That dry run does not qualify a provider or its price.

Give each worker the immutable registrations. Stage only its assigned shard on a trusted host. Example for worker 0 of 2:

```sh
i=0
uv run python -B -m examples.ship_handover.study emit "$work/stock.json" --out "$work/stock-w$i" --shard "$i/2"
uv run python -B -m examples.ship_handover.study emit "$work/inflight.json" --out "$work/inflight-w$i" \
  --shard "$i/2" --task "$work/ship-inflight"
python3.12 -BS -m examples.native_worklist status \
  --study "$work/stock.json" "$work/stock-w$i" "$i/2" --study "$work/inflight.json" "$work/inflight-w$i" "$i/2"
```

Use the same arguments with `take` in place of `status`. It emits one config. Start it once with `uv run harbor trial start -c "$cfg"`, subject to your site limits. Exit 3 from `take` means no local ticket remains. Never submit a ticket again after a crash. A copied worker directory cannot see claims on another disk.

Stop writers before exporting. On each worker:

```sh
python3.12 -BS -m examples.ship_handover.transfer_snapshot "$work/stock.json" "$work/stock-w$i" > "$work/stock-packet-$i.json"
python3.12 -BS -m examples.ship_handover.transfer_snapshot "$work/inflight.json" "$work/inflight-w$i" \
  --task "$work/ship-inflight" > "$work/inflight-packet-$i.json"
```

Give the recipient the registrations and packets. The recipient uses `transfer` as above. It needs no native trial files, keys, Docker, or Harbor installation. Each packet is a producer claim, not a signature. The recipient rejects two packets that claim the same ticket. A missing packet is not a zero.

## Which outcome is comparable?

Only a finished trial with two explicit native `returned` dispositions, no recorded errors, and a matching native and replayed grade contributes a known binary outcome. A verified physical zero from a cancelled seat remains a physical zero. It does not establish what a returning team achieved. The packet preserves the grade separately. Unknown historical dispositions stay unknown. All 26 previous stock control trials kept their physical grades; none proved both seats returned. The original stock collector and contrast still reproduce their previous text and JSON byte for byte.

Use `examples.ship_handover.collect REG ROOT ... [--task TASK] --json` to see decision chronology. The null receipt means no pre-decision receipt. The string `none` means the inbox returned before the decision without a reference. Neither establishes attention. The two tasks use independent first attempts, not identical sampled actions. This study does not estimate a model effect.
