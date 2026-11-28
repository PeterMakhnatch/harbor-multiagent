# Loader

Commit one load to the warehouse bay where it actually is. The warehouse has exactly 2 bays. Your team has a depot. The depot watches the floor after you stage the load. The relay is the only shared channel.

Run `python /app/recall.py view` to read your view. All commands print JSON. The `turn` field shows the current step: `stage`, `report`, `commit`, `close` or `done`. The `active` field is `true` when the current step is yours. The `bays` field lists the 2 bays. The `initial_bay` field shows where the load starts.

1. On the `stage` turn, run `python /app/recall.py stage BAY` with a bay from `bays`. When you stage, the load either stays at `initial_bay` or moves to the other bay. Each is equally likely. Your choice of `BAY` does not move the load.
2. The depot then files one report. It can name a bay or stay silent.
3. Run `python /app/recall.py wait commit`. On the `commit` turn your view has a `notice` and a `ticket`. The `notice` is a bay name or `--`. `--` means the relay delivered no bay to you on this turn. The relay may or may not pass the depot's report to you on this turn. Run `python /app/recall.py commit BAY TICKET` with one bay from `bays` and the `ticket` from that same view. Commit once. You cannot change the commit.
4. Run `python /app/recall.py wait close`. On the `close` turn your view has a new `notice` and a new `ticket`. Run `python /app/recall.py close TICKET` to finish.

The team succeeds only if the committed bay is the bay where the load actually is. The close step does not change the result. A commit that never happens counts as a failure. A ticket works only for the turn whose view showed it. Viewing again on the same turn shows the same ticket.

Do not attempt to access other roles or any channel outside the relay. The relay does not return a score. A later report cannot change your committed bay.
