# A team can earn a real reward without either seat running

The two native shipping seats used the same task bytes and runner policies. The old runner could not execute a private solution upload as the seat user. Both seats raised exit 126. The native verifier still awarded **1** when the supplier had already dispatched. The verifier awarded **0** when it had not. Neither grade measures seat participation.

| Runner | Host file modes | Supplier and channel | Native seat results | Replayed team reward |
|---|---|---|---|---:|
| Old | Private | Dropped, deliver | Both raised (126) | 0 |
| Old | Private | Committed, deliver | Both raised (126) | 1 |
| Candidate | Private | Dropped, deliver | Both returned | 1 |
| Candidate | Private | Dropped, withhold | Both returned | 0 |
| Candidate | Portable | Dropped, deliver | Both returned | 1 |
| Candidate | Portable | Dropped, withhold | Both returned | 0 |

**Primary test: 6/6 as registered.** Each ticket ran once on local Docker. All 6 native IDs and supplier runs differed. The trusted stock verifier reproduced all 6 observations and rewards from the saved ledger fields. Stock does not export a separate cut file. The 12 Git blobs, executable classes, native digest, and checksum matched. Both source copies kept their original bytes and file modes.

## Use this in a team benchmark

Inspect both `seat_results[].execution_disposition` and `reward_valid`. Keep a verified world outcome even when a seat fails. Do not count it as a completed two-seat intervention. Missing exit evidence is unknown. The [earlier seat-exit study](../seat-exit-contract/results.md) still passes **7/8**, not 8/8. These are new tickets, not replacements.

The fork prepares a private upload for the actual numeric, non-root POSIX seat user. It makes the uploaded script readable and executable for that owner, without opening the host checkout or broadening group or other access. Generic runners and Windows keep their existing path. These controls cover the stock parcel task on one Docker engine. They do not qualify other container engines, remote filesystems, or all task layouts.

The [registered comparison](registration.json) predates the runs. The [outcomes](outcomes.json) distinguish reported exit codes from normal returns. An absent exit-code file does not imply process exit 0. After a generic-environment compatibility correction, the [final source check](compatibility.json) used its own ticket. Both seats returned and the independent grade was 1. All 21 existing Oracle and OS checks passed. No model ran. Cost: $0.
