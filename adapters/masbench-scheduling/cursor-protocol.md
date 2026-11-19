# Calendar disclosure with a cursor

This experiment compares 3 deterministic protocols on the same 5 seats and the same start ticks.
It does not use a language model, and it does not claim a security or privacy guarantee.

- **Full:** Each seat sends its complete sorted list of feasible starts. The coordinator chooses the earliest common start.
- **First8:** Each seat sends at most its first 8 feasible starts. The coordinator chooses the earliest common start in the reports.
- **Cursor:** Start at bound 0. Send the bound to every seat and collect all 5 replies. A seat returns its first feasible start at or above the bound, or `null` if it has none. Stop with no common start if a seat replies `null`. If all replies agree, choose that start. Otherwise, set the bound to the largest reply.

The cursor coordinator receives only the public horizon and the seat callables. Each callable has its own private iterator.
The local simulator validates the finite local sequence before it opens the iterator. Each iterator advances only as needed for a reply.
The coordinator checks reply types, tick ranges, and contradictions with its last replies. It cannot check whether a seat skipped an undisclosed start.

For any common start at or above a bound, every seat's next start must be at or before that common start.
Thus, the largest reply cannot skip the earliest common start. If replies differ, the largest reply is strictly above the old bound.
With honest seats and a finite horizon, the cursor terminates and returns the same earliest start as full disclosure, or `null` if none exists.
First8 can omit a common start without proving that no common start exists.

## Run

Use the full pinned MASBench repository root, not the `Scheduling` subtree. Run from the Harbor worktree root:

```sh
python3 adapters/masbench-scheduling/cursor.py analyze --dataset "$MASBENCH_ROOT"
```

Add `--jsonl "$NEW_EVIDENCE_FILE"` to write one compact JSON object per case, then the summary. The command does not replace an existing file.
The summary also goes to standard output. The optional file does not include the private calendars, the reported lists, or the cursor transcript.

Each case record gives the choice and measurements for each protocol. It also shows whether full and cursor choose the same start, including if both choose `null`.
A non-null choice scores `1` only if it is in the official reference. The case duration fixes the end tick. All other choices score `0`.
The physical check uses the busy ticks, the duration, and the day boundary. It has value `null` when the protocol selects no interval.
The summary counts those outcomes and gives the total, mean, and maximum of each resource measure across the cohort.

## Resource measures

| Field | What it counts |
| --- | --- |
| `round_trips` | Sequential request-and-reply rounds. The 5 seat requests can run in parallel in a cursor round. The simulator always collects all 5 replies, including in the terminal round. Each one-shot arm takes 1 round. |
| `seat_exchanges` | The number of seat requests and their corresponding replies, counted as pairs. This equals `round_trips` times 5 for the cohort. |
| `scalar_disclosures` | Integer start ticks in replies, including repeated replies from the same seat. This excludes request bounds and `null`. |
| `distinct_disclosed_starts` | Unique pairs of seat and disclosed start tick. The same tick from different seats gives different facts. |
| `request_payload_bytes` | Bytes in each copy of the cursor's bound. The one-shot arms have an implicit request with no application payload. The protocol mode is fixed in advance for all arms. |
| `response_payload_bytes` | Bytes in each seat reply. This counts the digits, the `null` marker, and the brackets and commas in one-shot arrays. |
| `payload_bytes` | Request plus response bytes. Each payload uses compact JSON encoded in UTF-8. This excludes role labels, message framing, newlines, file metadata, and the final decision. |

Two synthetic cases illustrate different costs. They do not measure the MASBench cohort.

- When only tick 19 is common and the first 2 seats omit it in First8, full and cursor select 19. First8 selects no interval. Cursor uses 2 rounds and 33 bytes. Full uses 1 round and 58 bytes.
- When the 5 seat lists are `[1,5]`, `[2,4]`, `[4,5]`, `[2,5]`, and `[3,5]`, neither full nor cursor finds a common start. Cursor uses 3 rounds and 33 bytes. Full uses 1 round and 25 bytes.

## Limits

Fewer disclosed ticks do not imply fewer bytes or less time. Cursor rounds depend on prior replies, so extra rounds can increase wait time.
Bounds, replies, exhaustion, and termination can reveal information about a seat's availability. The simulator does not prevent a coordinator from inferring it.
These payload measures do not estimate model tokens, attention, memory, or cost. They do not count local calendar processing.
The coordinator and seats are functions in one Python process, not isolated Docker seats. The analysis does not provide native trial evidence, transport overhead, or measured network latency.
