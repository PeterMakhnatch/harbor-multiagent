# Seat exit contract: results

Machine record: `outcomes.json`. Registration: `registration.json`, committed in `8806a6319696d27840e6dff08867050b44bc7136` before the first run.

## Verdict

- The strict preregistered test fails. 14 of 16 seats and 7 of 8 attempts match the registered dispositions.
- The failed attempt is `unmodified-ship-handover`. Both seats recorded `raised` with `NonZeroAgentExitCodeError`. The registration predicted `returned`.
- All 7 controlled hello-seats scenarios pass. Each disposition matches the registration and the independent control events.
- For 16 of 16 seats, `execution_disposition` agrees with the independent witness: wrapper control events, or the seat exit code and Oracle log.
- The secondary diagnosis is not counted. It does not replace the failed first attempt.

## First attempts

| Case | Task | Tested seat | Supporting seat | Grade | Trial exception | Independent witness | Match |
| --- | --- | --- | --- | --- | --- | --- | --- |
| support-held | hello-seats | p1 `returned` | p2 `stop_requested` | valid, reward 1.0 | none | p2 held, then its hold was cancelled after p1 returned | yes |
| support-returned | hello-seats | p1 `returned` | p2 `returned` | valid, reward 1.0 | none | p2 wrapper returned before p1 returned | yes |
| support-self-cancel | hello-seats | p1 `returned` | p2 `cancelled` | valid, reward 1.0 | none | p2 raised its own CancelledError; no stop request | yes |
| support-raised | hello-seats | p1 `returned` | p2 `raised` | valid, reward 1.0 | SupportSeatFault | p2 raised a managed fault after its Oracle work | yes |
| team-deadline | hello-seats | p1 `stop_requested`, AgentTimeoutError | p2 `stop_requested` | valid, reward 1.0 | AgentTimeoutError | both seats held; both holds cancelled at the native deadline | yes |
| trial-cancel | hello-seats | p1 `stop_requested` | p2 `stop_requested` | invalid, no rewards | CancelledError | driver cancelled the trial; host cancel hook fired | yes |
| support-swallows-stop | hello-seats | p1 `returned` | p2 `stop_requested` | valid, reward 1.0 | none | p2 caught the stop and its wrapper returned normally | yes |
| unmodified-ship-handover | ship-handover | outgoing `raised`; relief `raised` (both tested) | none | valid, reward 0 | NonZeroAgentExitCodeError | both seats exit 126; Oracle log: `/solution/solve.sh: Permission denied`; verifier outcome `omission` | no |

Grades are the verifier's own values. The reader reports them apart from dispositions. No reward comes from a helper exit: in support-raised, reward 1.0 is the verifier grade.

## Secondary diagnosis (not counted)

`shipment-exit-committed-modes` used the same 12 ship-handover blobs and Git tree `c09e2f59e06ac324e5d9f2dbdc74d4da423de08e`. Only the file modes changed: shared 0644/0755 instead of the checkout's owner-only 0600/0700. Its prediction was written before the run.

| Seat | Disposition | Grade | Verifier outcome |
| --- | --- | --- | --- |
| outgoing | `returned` | valid, reward 1 | `exactly_one`, one supplier effect (`original`) |
| relief | `returned` | | |

Inference: the first-attempt failure is a deployment permission fault. The non-root seat user cannot run an owner-only `solve.sh`. The native lock digest is the same for both runs, so it does not show the mode difference.

## What the field means

`execution_disposition` records how the seat's play scope ended. It is not process death, a grade, or proof of a voluntary finish. In support-swallows-stop, p2 caught the stop and returned normally, and the field still reads `stop_requested`. Read `reward_valid` and `verifier_result.rewards` separately.

## Operator use

From the checkout, without installing Harbor:

```sh
python3 -BS -m examples.seat_outcomes TRIAL_DIR [TRIAL_DIR ...] --json
```

The reader prints, for each trial: grade validity, the original rewards, the trial exception type, and each seat's role, disposition, exception type and native timeout. Exit 0 means every trial, seat and grade label was readable. Exit 1 means at least one was unavailable or malformed. Exit 2 is a usage error. A missing `execution_disposition` reads as `unknown`. The reader does not invent an exit.

## Pins

- Runtime: fork commit `1553c1d61e3b66ee8b02ec4bd5e9ad220ad7effc`, `src/harbor` tree `9b9f4bd63ce05057dcf7e56fbda34aa567c2187c`, Harbor 0.26.0, Python 3.13.17. The 5 source blobs pinned by the hello-seats runs match this commit.
- hello-seats: Git tree `43928b740204e7b74a0794c275d9c188add25a67`, 10 blobs in `outcomes.json`.
- ship-handover: Git tree `c09e2f59e06ac324e5d9f2dbdc74d4da423de08e`, 12 blobs in `outcomes.json`.
- Both task trees are equal at the registration commit and at the workbench head.
- Reader: `examples/seat_outcomes.py` blob `9be3b2ab25b408596b75f0da9d01639219e62c7d`.

## Procedure

1. Stock Oracle: Harbor's unmodified `oracle` agent runs `/solution/solve.sh` in each seat. No model or provider is used.
2. Host wrapper (hello-seats only): a host driver ran each seat's stock Oracle to completion. Then it applied the case plan and logged control events from the seat wrappers and host hooks. The control events do not read `execution_disposition`. The driver is pinned by sha256 only; it is not in the repository.
3. ship-handover: `harbor trial start` with stock Oracle in both seats and no wrapper.

## Older inputs: separate cold read on the original native directories

| Population | Trials | Seats | Disposition unknown | Grades |
| --- | --- | --- | --- | --- |
| Archived native model runs (8 locks, 192 slots; 12 unmaterialized slots excluded) | 180 | 360 | 360 | 171 valid: 65 ones, 106 zeros; 9 invalid without rewards |
| Older scripted trials | 18 | 36 | 36 | 10 ones, 8 zeros |

These are aggregate results from `examples.seat_outcomes` on private native files. A recipient can check the totals but cannot reconstruct an old seat exit. Missing directories are not two more unknown seat records.

Fork regressions: `tests/unit/trial/test_seats.py` passed 45 of 45 in the fork environment with the project SDK. An earlier ad hoc runtime had 2 import failures in package metadata, not in seat code.

## Gaps

- The ship-handover runs captured no source-blob pin. Their runtime bytes are not proven equal to `1553c1d`.
- Each case has one scripted attempt. The study measures no model behavior and no rates.
