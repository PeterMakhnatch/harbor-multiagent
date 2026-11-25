# Depot

Help the loader dispatch the realized museum touring brief. You have private crate-to-bay records. The registrar has private exhibit-to-crate records. The relay is the only shared channel.

Run `python /app/relay.py wait depot`. The JSON view contains your `cards`, the `agenda` if available, and the `quota` for your filing. A card has an `id`, crate `key`, bay `value`, and inclusive epoch interval (`start`, `end`). Records are not in chronological order.

Each agenda entry has a crate `key`, an `epoch`, and a positive `weight`. It represents nominated demand that the registrar filing supports. Select distinct card IDs within your quota to cover as much important demand as possible. A record covers an entry only when the key matches and the interval contains the epoch. A record can cover multiple entries. The agenda does not reveal the realized brief. If the agenda is empty, use the available public fields and private cards to make your best selection.

Some views include `bundles`. Each row reports a pair of crates, an epoch, and a weight. If `bundle_source` is `registrar`, the registrar chose the rows. Both the pair and its weight can be wrong. Otherwise the relay derives the rows from the forecast and the filed records. A weight of `0` means unavailable, not zero demand. An empty list supplies no pairs. The team earns a point only if both exhibits reach the correct bays.

Run `python /app/relay.py file ID ...` with at most `quota` distinct card IDs. Omit IDs to file nothing. File once. The relay may deliver the records to the loader, depending on the channel configuration.

Do not attempt to access other roles or any channel outside the relay. Record IDs and label order have no documented meaning.
