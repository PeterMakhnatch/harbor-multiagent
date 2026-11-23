# Finance seat

You are the finance director of an online retail company in a simulated market. The company aims to end its three-year run worth as many times its seed money as possible, without going bankrupt. Its score is the base-32 logarithm of that multiple, clipped to 0..1.

The company has run `completed_days` days under a fixed public pricing rule. The same rule continues for the rest of the run, and the team must choose one price level for all products:

1 = keep prices (1.00x)
2 = raise all prices by 15% (1.15x)
3 = lower all prices by 15% (0.85x)

Product range, advertising and credit stay unchanged. The sales analyst recommends one option. You make the final choice.

1. Send `GET http://world:8080/view` from this seat. If the server returns HTTP 409, wait 1 second and retry. Stop waiting after 480 seconds.
2. Read `completed_days`, your finance `observation` and `advice`. The `advice` field is the option the sales analyst recommends.
3. Choose the final option: 1, 2 or 3. You can follow or ignore the advice.
4. Compute the SHA-256 digest of the full parsed `/view` response. Serialize the value with the Python options below.
5. Send `POST http://world:8080/submit` with `Content-Type: application/json`. Set `X-SeatLab-View-SHA256` to the digest. Send the body `{"pick": N}`, where `N` is the integer 1, 2 or 3.
6. Stop when the server returns HTTP 200.

Use `python3` and its standard library to send requests. The server accepts only the first valid submission from this seat. It rejects every later submission with HTTP 409, so do not submit again after HTTP 200.

```python
import hashlib
import json

canonical = json.dumps(
    view, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
).encode("utf-8")
digest = hashlib.sha256(canonical).hexdigest()
```

Here `view` is the full parsed response. The header acknowledges its canonical JSON, not the original response bytes.
