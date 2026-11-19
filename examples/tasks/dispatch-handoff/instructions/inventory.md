# Inventory seat

For each shipment, report the bay availability at revisions 1 and 2. You do not select a bay.

1. Send `GET http://world:8080/view` from this seat. Each entry in `cases` has an integer `id` and a `reports` list.
2. Send `POST http://world:8080/submit` with `Content-Type: application/json`. Send one entry for each case in a single `rows` array. Copy each `id` and its two reports without changes.
3. Stop when the server returns HTTP 200.

For example, send this body for a batch with 1 case:

```json
{"rows":[{"id":0,"reports":[{"revision":1,"available":["A","C"]},{"revision":2,"available":["B","C"]}]}]}
```

Use `python3` and its standard library to send requests. You cannot change an accepted submission.
