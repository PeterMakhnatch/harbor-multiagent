# Does the loader need both teammates?

The registrar knows which crate holds each exhibit. The depot knows where each crate is. The loader requests a brief and dispatches 2 exhibits to physical bays. The native verifier rewards 2 correct destinations, not a persuasive explanation. A lucky guess counts. A message is useful here only if it can change the physical decision.

[The earlier warehouse control](../physical_dispatch/README.md) established that the depot can matter. It kept the registrar’s information constant. This variant independently changes each teammate’s private map. It tests whether either teammate can replace the other.

## Check the benchmark before running agents

From the fork checkout, with Python 3.12 or later:

```sh
python3.12 -BS -m examples.seatlab.joint_handoffs certify --start 7
```

No installation, Docker, key or provider is needed. The check uses the task engine, physical grader and actual bytes sent to the loader. It examines at most 128 ordinary lottery draws. It accepts the first draw that realizes the publicly nominated brief. Starting at 7 selects development seed 10 after 3 rejections. This brief has natural probability 3/10. Starting at 19 selects seed 19 immediately; its probability is 2/5. These are public development cases, not held-out cases.

Each accepted seed gives 4 equally weighted maps. A single change exchanges the right routes; changing both maps restores the original routes. Within each map, the 4 delivery conditions keep the legal actions, physical grade, nomination, filed IDs and depot agenda fixed. Hidden record values become `?` of equal UTF-8 byte length. The loader still sees keys, IDs and record ages. The verifier keeps the original values.

| Filed values delivered | Distinct final requests across 4 maps | Best reward knowing the 4-map distribution | Exact reward of the example rule below |
|---|---:|---:|---:|
| Neither | 1 | 1/2 | 1/56 |
| Registrar only | 2 | 1/2 | 1/56 |
| Depot only | 2 | 1/2 | 1/2 |
| Both | 4 | 1 | 1 |

**Read the two reward columns differently.** The best reward lets a decision rule know the restricted maps and their payoffs. Even without either filing it can guess between the 2 winning dispatches. The loader does not receive this prior or the payoff table.

The example rule reads only its delivered view. Without visible depot values it chooses 2 distinct public bays uniformly. With depot values alone, it tries the 2 possible assignments with equal probability. With both filings, it follows the record chains. The rule assumes the depot filed the 2 requested crates and that their bays differ. The fixed prefix satisfies this. The instructions do not promise it for free teams. The check averages actual payoffs over legal choices; it takes no random samples.

The values above hold on both development seeds. For each seed the checker grades all 64 legal final dispatches on each of 4 maps. It reuses those 256 grades under the 4 delivery conditions. All 16 final requests are 3,276 bytes on these seeds. Equal byte length is not equal token count. The [four-input author check](../decision_boundary/README.md#does-the-final-decision-need-both-handoffs) rejects mismatched payoffs and a joint request that reveals information absent from the two single-source requests together.

### Give another author the actual four tables

The private export contains the actual loader request and all native grades. Keep it out of participant contexts. To run the same check through the generic tool, without importing the warehouse code into that tool:

```sh
umask 077
author=$(mktemp -d /tmp/relay-author.XXXXXX)
python3.12 -BS -m examples.seatlab.joint_handoffs export-worlds "$author/tables" --start 7
python3.12 -BS -m examples.seatlab.decision_boundary joint --expect-each-needed yes \
  --files "$author/tables/neither.json" "$author/tables/first.json" \
          "$author/tables/second.json" "$author/tables/both.json"
```

`first` is the registrar; `second` is the depot. Another benchmark can produce the same 4-file format without depending on Exhibit Relay. The generic check compares score tables and byte partitions. It cannot certify a different benchmark’s role isolation, true outcome, or the meaning of its action labels.

### What a masked record still reveals

Keep both values hidden and fix the depot’s filing count at 1. Filing the record with the first crate key gives the loader 1 distinct request. Filing the record whose current bay sorts first instead gives 2 distinct requests. The identity of the chosen crate reveals which bay map the depot saw. In both cases the request has 3,197 bytes on these seeds. This check measures visible information, not intent or reward. Do not call this condition “no communication.”

## Prepare one native cell without running it

Use an absent task path and output paths outside the checkout. For `prepare-*`, use a Python environment with this fork’s Harbor dependencies. These commands do not start participants or connect to owner sockets.

```sh
umask 077
work=$(mktemp -d /tmp/joint-relay.XXXXXX)
mkdir -m 700 "$work/owners"
python3.12 -BS -m examples.seatlab.joint_handoffs materialize "$work/task"
python -B -m examples.seatlab.joint_handoffs prepare-receiver "$work/task" "$work/fixed" \
  --seed 10 --registry-map original --bay-map swapped --channels depot \
  --loader-dispatch "$work/owners/final.sock"
python -B -m examples.seatlab.joint_handoffs prepare-team "$work/task" "$work/free" \
  --seed 10 --registry-map original --bay-map swapped --channels depot \
  --loader-request "$work/owners/request.sock" --registrar "$work/owners/registrar.sock" \
  --depot "$work/owners/depot.sock" --loader-dispatch "$work/owners/final.sock"
```

`prepare-receiver` fixes the nomination and both filings. Only the final loader choice belongs to an external participant. It refuses a nonfocal seed rather than substituting another case. `prepare-team` gives all 3 roles discretion over all 4 turns, including on nonfocal draws. The 2 designs have distinct receipts. Each writes a private `config.json`, `lock.json` and `receipt.json` for one cell. The receipt is not an enrollment, behavioral result or proof of provider identity. Never put the private configuration, map labels, receipt or certificate in the participants’ contexts.

For a later receiver study, reserve held-out seeds before reading any model outputs. Keep all 16 map and delivery cells per seed. Randomize order and isolate participant state between cells. Measure actual physical reward for every assigned attempt, and report missing attempts separately. For a later free-team study, keep ordinary lottery draws. Measure nomination, filings, what reached the loader and actual dispatch separately. A free team can change the selected records or agenda, so the fixed-prefix table is not its expected score. Do not mix the two designs. No frontier-model difficulty or behavioral effect has been established.

### A test to register before the first model call

For a fixed receiver, ask: **does making either source available improve the physical outcome when the other source is already present?** Freeze the model version, system prompt, retry rule, isolated session policy, number of independent seed blocks and spending limit first. Keep the uncensored list of assigned cases. Keep development seeds out of that list.

One possible primary test uses the seed, not the 16 cells, as the independent unit. For each seed, average reward over the 4 maps within each delivery condition. Compute 2 paired differences: `both − depot` and `both − registrar`. Report the differences, ties and both arm means for each comparison. For each difference, test whether a positive sign occurs more often than a negative sign across independent seeds. Use the exact one-sided sign test at 0.05; remove ties only from its denominator. All ties give no evidence. Require **both** tests to pass for the joint claim. This tests the frequency of a positive paired difference, not just mean reward.

Do not declare a pass with an unverified outcome. Report missing assignments and the possible reward range if their grades were 0 or 1. A correct unsupported guess is still a correct dispatch. An incorrect dispatch with both chains present is a receiver error, not proof of what the model attended to. No study has been enrolled or run for this proposal.

To test **when** a correction still helps, use [Dispatch Recall](../dispatch_recall/README.md). Its world delivers the same depot report before commitment, after commitment, or not at all.

When **both** teammates must commit, use the [coupled-retention benchmark](../coupled_retention/README.md). It checks whether two systems with identical per-seat inputs and accuracy can get different physical team rewards. It also measures what a pair of separate decision rules can achieve without pooling private context.
