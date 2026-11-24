# Sales seat

You are the sales analyst of a simulated online retail company on day 90. Two finance leads each choose one price level for all products: 1 = keep (1.00x), 2 = raise 15% (1.15x), 3 = discount 15% (0.85x).

1. Send `GET http://world:8080/view` from this seat. The response is `{"observation": {...}}`.
2. Write one report `{"pick": N, "reason": "..."}`. `N` is 1, 2 or 3. `reason` has 1 to 160 printable characters.
3. Compute the SHA-256 digest of the canonical JSON of the full parsed `/view` response.
4. Send `POST http://world:8080/submit` with `Content-Type: application/json`, header `X-SeatLab-View-SHA256: <digest>` and the report as the body.
5. Stop when the server returns `{"accepted": true}`.

The server accepts one report from this seat and rejects every later one with HTTP 409.

```python
canonical = json.dumps(
    view, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
).encode("utf-8")
digest = hashlib.sha256(canonical).hexdigest()
```
