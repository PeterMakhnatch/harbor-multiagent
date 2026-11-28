# Did the receiver lose a correction it already acknowledged?

Use this kit when building an evaluation of team memory or deciding what a handoff summary should keep. It separates three questions: **did a teammate send useful information, did the receiver retain it, and would reading the source again change the choice?** It runs offline with Python 3.12. It uses the unmodified [two-seat `revised-dispatch` task](../../tasks/revised-dispatch/). No provider, containers or Harbor install are needed for these checks.

The motivating example is a [Bespoke MAC-Bench Mars Return trace](https://benchmarks.bespokelabs.ai/multi-agent-coordination-bench/#task-mars-return). A participant proposed a landing-gear change that teammates had ruled out. It later said it had missed their earlier messages. That is a reason to test retention, not proof that compression caused the error.

## Check what one retained fact can change

From the checkout root:

```sh
python3.12 -BS -m examples.seatlab.receiver_memory native \
  --seed 7 --sender truthful --expect-retention yes
python3.12 -BS -m examples.seatlab.receiver_memory native \
  --seed 7 --sender repeat --expect-retention no
```

The reporter sends an initial slot. The booker reads the report, returns its receipt and drafts that slot. The reporter then sends a second report. The booker reads both and acknowledges the second. **Every window ends at the same successful acknowledgement**, before the booker commits. Only the earlier context varies. The adapter preserves the booker's own method, path, body, status, and reply. It replaces random receipt strings consistently; it does not claim to reproduce HTTP or provider prompt bytes.

- `full` retains all earlier booker frames. `none` retains only the common instruction and current acknowledgement.
- `oldest` and `newest` each retain one slot copied from earlier inbox replies. The kit compares both the encoded note length and the whole packet length. Equal bytes do not imply equal tokens.
- `refresh` drops earlier frames, then adds the reply to one **new legal inbox request** after the acknowledgement. This is a recovery control, not an equal-cost memory arm.

The adapter calls the native world and verifier for every legal commit: 3 slots × 2 hidden outcomes × 2 read schedules. The physical scores must match across read schedules. The native verifier checks the ordered handoffs, not just the chosen letter. With a truthful sender, full history, the newest note, and a fresh read each permit reward 1. The oldest note and no note each permit only 1/2. When the sender repeats the first slot regardless of the new fact, all five windows permit only 1/2. These are optimal values under 2 equally likely worlds and a known sender rule. They are not model scores.

## Can the receiver tell *when* to read again?

Retain one slot, sometimes from the initial report and sometimes from the revision. Include a one-character report-position tag. The `version` arm gives the true position, the `erased` arm gives `?`, and `sham` gives a balanced, independent `1` or `2`. Every other byte of a given offer is the same. The receiver can commit immediately or spend one read to see the real inbox before committing. The native score stays unchanged. A separate utility subtracts a **hypothetical** 1/4 reward point per read.

```sh
python3.12 -BS -m examples.seatlab.receiver_memory.cohort \
  --start 0 --sender truthful --expect-version-benefit yes
python3.12 -BS -m examples.seatlab.receiver_memory.cohort \
  --start 0 --sender repeat --expect-version-benefit no
```

The cohort includes all 6 directed replacements of A, B and C. For each seed it crosses confirm/replace, older/newer note and both independent tag assignments: 48 equally weighted rows per arm. These are designed cases, **not 48 independent trials**. `--start 200` selects different seeds for the same six rotations, not a new task. `never`, `always`, and `adaptive` use the best finite choice given the declared distribution. `adaptive` chooses whether to read using only its initial observation. A tie skips the read. Inspect physical reward, read fraction, and utility separately; do not report utility as the native grade.

The bounds can use knowledge of the sender rule and tag assignment. A participant does not receive either. For a stronger control, `public_wire_policies` runs the **same** rule on the actual packets in every arm: commit the retained slot on tag `2`; otherwise read and commit the reported revision. It has no access to the task, seed, arm or payoff table. With truthful reports, the valid and sham tags both trigger a read in half the cases. The valid tag lets this rule commit the correct slot in all cases, versus 7/8 with the sham tag. This is a scripted reference, not an agent score. The `always` and `never` public controls remain the same across arms.

## Give a receiver one decision without giving it the answer

Only the operator may see the seed, sender rule, world label, payoffs or filenames below. Supply only role packets to the receiver. Do not run an untrusted participant in the operator's checkout. These commands separate data in a cooperative replay; they do not isolate processes.

```sh
umask 077
work=$(mktemp -d)
python3.12 -BS -m examples.seatlab.receiver_memory packet \
  --seed 7 --sender truthful --world replace --policy oldest > "$work/request.json"
printf '%s\n' '{"slot":"A"}' > "$work/answer.json"
python3.12 -BS -m examples.seatlab.receiver_memory grade \
  --seed 7 --sender truthful --world replace --answer "$work/answer.json"
```

The one-step contract is to return just `{"slot":"A"}`, `B`, or `C` in the same format. The example uses an arbitrary A, not an oracle. Preserve the native role instruction as historical task context. Do not give the receiver real tools in this one-step replay.

For a two-step decision, connect the receiver to the following command through pipes. Its first stdout line is the offer. Only `{"op":"read"}` unlocks a second line containing the actual reread. A commit must be `{"op":"commit","slot":"A"}` with a legal slot. The private grade goes to a new operator file, never to receiver stdout. These two fixed inputs demonstrate the protocol without connecting a model:

```sh
printf '%s\n' '{"op":"read"}' '{"op":"commit","slot":"A"}' | \
  python3.12 -BS -m examples.seatlab.receiver_memory.provenance \
    --seed 7 --sender truthful --world replace --age oldest \
    --arm version --coin 2 --price 1/4 --outcome "$work/outcome.json"
cat "$work/outcome.json"
```

## Use your own task's role history

```sh
python3.12 -BS -m examples.seatlab.receiver_memory export \
  --seed 7 --sender truthful --out "$work/author-only.json"
python3.12 -BS -m examples.seatlab.receiver_memory inspect \
  "$work/author-only.json" --expect-retention yes
```

Use the exported JSON as the input contract. Each `case` supplies a private name, positive mass, instruction, ordered reader frames, one possible refresh frame, and a common action-to-score table. `selector` identifies the read method, path, sequence of JSON object keys, and the field to retain from each returned list entry. `history` ends at the reader's last actual pre-commit frame. `refresh` must be a separate legal read on a matched world. `inspect` checks the constructed windows; it cannot authenticate observations or physical grades. Supply native evidence for your own adapter. Exit 0 means the supplied expectation holds, 1 means it does not, and 2 means the input is invalid.

For optional reads with a different cost or task, use the [two-census checker](../decision_boundary/README.md#when-is-one-more-read-worth-its-price). For delayed delivery rather than loss after receipt, use [Dispatch Recall](../dispatch_recall/README.md). None of these controls measures how a provider compacts context, whether a receiver attends to a fact, or what an unrestricted team will do.
