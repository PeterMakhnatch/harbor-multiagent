You are the builder. Send `GET http://world:8080/builder`. If the status is 409, wait 1 second and try again. Stop if 60 seconds pass without a successful response.

The successful response is a JSON object with `choices`, the kit IDs, and `inboxes`, an array of 24 entries in case order. Each entry has `left` and `right` fields. Each field is a pair of allowed kit IDs or `null` if that review is not available to you.

For each case, use the kit ID that appears in both reviews if there is exactly 1 such ID. Use `K1` if either review is absent or there is no unique match.

Send `POST http://world:8080/builder` with `Content-Type: application/json`. Send a JSON object with a `kits` array containing the 24 kit IDs in case order. Stop after a successful response.
