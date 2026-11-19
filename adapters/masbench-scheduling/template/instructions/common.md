# Schedule a meeting

You are `$role`. Wait for `/exchange/input.json`, which contains your private calendar view. Each tick is 10 minutes. The `busy` list contains occupied tick indices.

A feasible meeting starts at a nonnegative integer tick. It fits entirely within `horizon` ticks and within one day of `slots_per_day` ticks. None of the `duration` consecutive ticks starting at that tick can be busy.

Use the `$mode` policy. $policy List the start ticks in ascending order without duplicates. Use an empty list when there are no starts to report.

Write one JSON object to `/exchange/out/report.json` with these fields:

- `role`: `"$role"`.
- `view_sha256`: the lowercase SHA-256 hex digest of the calendar view.
- `starts`: your list of start ticks.

To calculate a view digest, serialize the received JSON object with Python `json.dumps(view, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)`. Encode the result as ASCII with no trailing newline. Hash those bytes.

Write each submission to a temporary file in `/exchange/out`, then rename it to its final name in that directory. Do not submit the same file twice.

${scheduler}Wait for `/exchange/done.json` before ending your work.
