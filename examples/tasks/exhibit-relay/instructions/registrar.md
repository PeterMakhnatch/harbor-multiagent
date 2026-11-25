# Registrar

Help the loader dispatch the realized museum touring brief. You have private exhibit-to-crate records. The depot has private crate-to-bay records. The relay is the only shared channel.

Run `python /app/relay.py wait registrar`. The JSON view contains your `cards`, nomination `briefs` if available, and the `quota` for your filing. A card has an `id`, exhibit `key`, crate `value`, and inclusive epoch interval (`start`, `end`). Records are not in chronological order.

Each nominated brief has an epoch, weight and 2 exhibit IDs. Select distinct card IDs that support the important briefs within your quota. A record supports an item only when its key matches that exhibit and its interval contains the brief epoch. One card may support multiple briefs. Think about the combined weight of briefs you can support; the realized brief is drawn in proportion to weight. The realized brief is not available yet. If nominations are absent, use the available public fields and private cards to make your best selection.

Run `python /app/relay.py file ID ...` with at most `quota` distinct card IDs. Omit IDs to file nothing. File once. The relay may turn your filing into a depot agenda and deliver records to the loader, depending on the channel configuration.

Do not attempt to access other roles or any channel outside the relay. Record IDs and label order have no documented meaning.
