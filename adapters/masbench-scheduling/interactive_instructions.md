# Schedule a meeting with five seats

You are `$role`. Use only your `/exchange` directory. Wait for `/exchange/input.json`. Check that `role` names your seat and `mode` is `cursor`.

Each tick is 10 minutes. A feasible start is an integer tick at or above 0. The next `duration` ticks must all be inside `horizon` and on one day of `slots_per_day` ticks. None of those ticks may be in your `busy` list. Do not send your calendar or the full list of starts.

## Answer each query

Rounds start at 1. In a file name, replace `NNN` with the 3-digit round number, such as `001`. The first bound is 0. Each later bound must be larger than the last bound and smaller than `horizon`. There are at most 720 rounds.

Wait for `/exchange/query-NNN.json`. It contains only `round` and `bound`. Write one object to `/exchange/out/reply-NNN.json` with these fields:

- `role`: `"$role"`.
- `round`: the round number.
- `next`: your first feasible start at or above `bound`, or JSON `null` if there is none.
- `initial_sha256`: the digest of your complete `input.json` object.
- `query_sha256`: the digest of the complete received query object.

Seats `m2` through `m5` repeat this step for each next query. They stop when the broker publishes `/exchange/done.json` with `{"done":true}`. They do not read a round bundle or submit a choice.

## Coordinate the rounds (m1 only)

Write `/exchange/out/query-001.json` with `round` set to 1 and `bound` set to 0. Set `view_sha256` to the digest of your complete `input.json` object.

Answer the forwarded query like every other seat. Then wait for `/exchange/round-NNN.json`. This bundle contains `round`, `bound`, and `replies`. The `replies` object has one start or `null` for each seat, `m1` through `m5`. Check that the bundle matches the query and includes your own reply without changes.

- If any reply is `null`, choose `null`.
- If all 5 replies are the same start, choose that start.
- Otherwise, write the next `/exchange/out/query-NNN.json`. Set `round` to the next round number. Set `bound` to the largest reply. Set `view_sha256` to the digest of the complete current bundle.

When you make a choice, write `/exchange/out/choice.json`. Set `round` to the current round number. Set `start` to the chosen start or `null`. Set `view_sha256` to the digest of the complete current bundle. Do not send another query. Wait for `/exchange/done.json` with `{"done":true}`.

## Format, publish, and wait

For each digest, serialize the JSON object with `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)`. Encode the result as ASCII without a trailing newline. Hash those bytes with SHA-256. Use lowercase hexadecimal.

Write each submission to a temporary file in `/exchange/out` with a `.submit-` prefix. Close the file. Use a hard link to publish it under its final name without replacing an existing file. Remove the temporary file. Do not change or submit the same final file twice.

Use one deadline for the whole exchange. Stop if the deadline expires or a required message is invalid. Do not report a missing reply as `null`.
