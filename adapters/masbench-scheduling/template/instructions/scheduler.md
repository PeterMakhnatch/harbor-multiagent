## Choose the meeting start

After you submit your own report, wait for `/exchange/proposals.json`. Its `reports` object contains one list of start ticks per participant. Find the earliest start in all 5 lists. Use JSON `null` if there is no common start.

Write one JSON object to `/exchange/out/choice.json` with `start` set to this tick or `null`. Set `view_sha256` to the digest of the complete proposals object, using the same rule as for the calendar view.

