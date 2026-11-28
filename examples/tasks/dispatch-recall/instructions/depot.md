# Depot

Help the loader commit one load to the warehouse bay where it actually is. The warehouse has exactly 2 bays. The loader commits the load. You watch the floor. The relay is the only shared channel.

Run `python /app/recall.py view` to read your view. All commands print JSON. The `turn` field shows the current step: `stage`, `report`, `commit`, `close` or `done`. The `active` field is `true` when the current step is yours. The `bays` field lists the 2 bays.

1. Run `python /app/recall.py wait report`. After the loader stages the load, your view has a `current_bay` field. It shows the bay where the load is now.
2. On the `report` turn, file one report. Run `python /app/recall.py report BAY` with a bay from `bays`, or run `python /app/recall.py report --silent` to send no bay. Report once.

The loader then commits. The relay may or may not deliver your report to the loader before the commit. The team succeeds only if the committed bay is the bay where the load actually is.

Do not attempt to access other roles or any channel outside the relay. The relay does not provide correctness feedback.
