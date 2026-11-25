# Loader

Dispatch the 2 exhibits in the realized touring brief to their correct bays. Your team has a registrar and a depot. Each has private records. The relay is the only shared channel.

Run `python /app/relay.py view` to read your view. All commands print JSON. The `phase` and `turn` fields show the current stage. The `nominate` and `quota` fields show the current caps. Each brief has an ID, an epoch, 2 exhibit IDs and a positive weight. The realized brief is drawn with probability proportional to its weight.

1. In the `request` phase, choose up to `nominate` distinct brief IDs. Run `python /app/relay.py request ID ...`. Think about total coverage: records can cover more than one nominated brief, and each source has a record cap.
2. Run `python /app/relay.py wait dispatch`. The registrar and depot each file records once. The dispatch view reveals `order`, the realized brief, and any records delivered to you.
3. For each exhibit in `order.items`, find the registrar record whose `key` matches the exhibit and whose inclusive `start` to `end` interval contains `order.epoch`. Its `value` is a crate. Find the depot record whose `key` matches that crate and whose interval contains that same epoch. Its `value` is a bay. Records may be incomplete or absent. Use the public bay list to make your best decision if evidence is missing.
4. Run `python /app/relay.py commit EXHIBIT=BAY EXHIBIT=BAY` with exactly the 2 realized exhibits. Commit once. The team succeeds only if both physical destinations are correct.

Do not attempt to access other roles or any channel outside the relay. Record IDs and label order have no documented meaning. The relay does not provide correctness feedback.
