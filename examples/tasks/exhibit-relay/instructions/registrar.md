# Registrar

Help the loader dispatch the realized museum touring brief. You have private exhibit-to-crate records. The depot has private crate-to-bay records. The relay is the only shared channel.

Run `python /app/relay.py wait registrar`. The JSON view contains your `cards`, nomination `briefs` if available, and the `quota` for your filing. A card has an `id`, exhibit `key`, crate `value`, and inclusive epoch interval (`start`, `end`). Records are not in chronological order.

Each nominated brief has an epoch, weight and 2 exhibit IDs. Select distinct card IDs that support the important briefs within your quota. A record supports an item only when its key matches that exhibit and its interval contains the brief epoch. One card may support multiple briefs. Think about the combined weight of briefs you can support; the realized brief is drawn in proportion to weight. The realized brief is not available yet. If nominations are absent, use the available public fields and private cards to make your best selection.

Run `python /app/relay.py file ID ...` with at most `quota` distinct card IDs. Omit IDs to file nothing. File once. The relay may turn your filing into a depot agenda and deliver records to the loader, depending on the channel configuration.

If your view includes `note_capacity`, use `python /app/relay.py file-notes '[]' ID ...` instead of `file`. Replace `[]` with up to 4 claims. Each claim is a JSON object such as `{"epoch":1,"keys":["crate A","crate B"],"weight":4}`. Use actual public crate labels. Report two crates from the same nominated brief, its epoch and weight. Use `0` for an unknown weight. The relay may withhold weights; it does not add or correct your claims. An empty list sends no claims. Card and claim quotas are separate.

Do not attempt to access other roles or any channel outside the relay. Record IDs and label order have no documented meaning.
