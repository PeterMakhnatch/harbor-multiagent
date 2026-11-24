# Finance seat

You are a finance lead of a simulated online retail company on day 90. You commit one price level for all products: 1 = keep (1.00x), 2 = raise 15% (1.15x), 3 = discount 15% (0.85x).

1. Send `GET http://world:8080/view` from this seat. If the server returns HTTP 409, wait 1 second and retry. The response is `{"observation": {...}, "report": ...}`. `report` is the sales report or `null`.
2. Compute the SHA-256 digest of the canonical JSON of the full parsed `/view` response.
3. Send `POST http://world:8080/submit` with `Content-Type: application/json`, header `X-SeatLab-View-SHA256: <view digest>` and the body `{"pick": N}`. `N` is 1, 2 or 3.
4. After `{"accepted": true}`, send `GET http://world:8080/receipt`. The response is `{"report": ..., "token": "<12 hex>"}`. `report` is the sales report or `null`.
5. Compute the digest of the full parsed `/receipt` response.
6. Send `POST http://world:8080/ack` with header `X-SeatLab-View-SHA256: <receipt digest>` and the body `{"ack": "<token>"}`.
7. Stop when the server returns `{"accepted": true}`.

The committed price is final. The server accepts one submission and one acknowledgement from this seat and rejects every later one with HTTP 409.

```python
canonical = json.dumps(
    value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
).encode("utf-8")
digest = hashlib.sha256(canonical).hexdigest()
```
