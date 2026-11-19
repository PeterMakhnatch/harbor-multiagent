# Dispatch seat

Select one dock bay for each shipment. Your goal is to select the available bay with the highest utility at revision 2.

1. Send `GET http://world:8080/view` from this seat. If the server returns HTTP 409, wait 1 second and retry. Stop waiting after 90 seconds.
2. For each entry in `cases`, read its integer `id`, its `utilities` for bays A through D, and its availability information. The newest report has the highest revision number, not necessarily the last position in `messages`. If `pooled_available` is not null, it lists the available bays at revision 2. If there is no availability information, use the utilities alone.
3. Compute the SHA-256 digest of the full parsed `/view` response. Serialize the value with the Python options below.
4. Send `POST http://world:8080/submit` with `Content-Type: application/json`. Set `X-SeatLab-View-SHA256` to the digest. Send one entry per case in a single `rows` array.
5. Stop when the server returns HTTP 200.

For example, send this body for a batch with 1 case:

```json
{"rows":[{"id":0,"bay":"C"}]}
```

Use `python3` and its standard library to send requests. You cannot change an accepted submission.

```python
import hashlib
import json

canonical = json.dumps(
    view, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
).encode("utf-8")
digest = hashlib.sha256(canonical).hexdigest()
```

Here `view` is the full parsed response. The header acknowledges its canonical JSON, not the original response bytes.
If you retry an accepted submission, send the same action and the same header.
