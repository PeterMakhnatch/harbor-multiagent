# Inventory seat

For each shipment, report the bay availability at revisions 1 and 2. You do not select a bay.

1. Send `GET http://world:8080/view` from this seat. Each entry in `cases` has an integer `id` and a `reports` list.
2. Compute the SHA-256 digest of the full parsed `/view` response. Serialize the value with the Python options below.
3. Send `POST http://world:8080/submit` with `Content-Type: application/json`. Set `X-SeatLab-View-SHA256` to the digest. Send one entry per case in a single `rows` array. Copy each `id` and its two reports without changes.
4. Stop when the server returns HTTP 200.

For example, send this body for a batch with 1 case:

```json
{"rows":[{"id":0,"reports":[{"revision":1,"available":["A","C"]},{"revision":2,"available":["B","C"]}]}]}
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
