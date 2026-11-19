# Dispatch seat

Select one dock bay for each shipment. Your goal is to select the available bay with the highest utility at revision 2.

1. Send `GET http://world:8080/view` from this seat. If the server returns HTTP 409, wait 1 second and retry. Stop waiting after 90 seconds.
2. For each entry in `cases`, read its integer `id`, its `utilities` for bays A through D, and its availability information. The newest report has the highest revision number, not necessarily the last position in `messages`. If `pooled_available` is not null, it lists the available bays at revision 2. If there is no availability information, use the utilities alone.
3. Send `POST http://world:8080/submit` with `Content-Type: application/json`. Send one entry per case in a single `rows` array.
4. Stop when the server returns HTTP 200.

For example, send this body for a batch with 1 case:

```json
{"rows":[{"id":0,"bay":"C"}]}
```

Use `python3` and its standard library to send requests. You cannot change an accepted submission.
