#!/bin/bash
set -euo pipefail

exec python3 - <<'PY'
import json
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

base = "http://world:8080"
http = build_opener(ProxyHandler({}))


def get_view():
    deadline = time.monotonic() + 90
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            with http.open(f"{base}/view", timeout=min(5, remaining)) as response:
                return json.load(response)
        except HTTPError as exc:
            exc.close()
            if exc.code != 409:
                raise
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))
    raise TimeoutError("Inventory did not submit within 90 seconds")


def choose_bay(case):
    available = case["pooled_available"]
    if available is None and case["messages"]:
        newest = max(case["messages"], key=lambda message: message["revision"])
        available = newest["available"]
    utilities = case["utilities"]
    return max(utilities if available is None else available, key=utilities.__getitem__)


view = get_view()
role = view["role"]
if role == "inventory":
    rows = [{"id": case["id"], "reports": case["reports"]} for case in view["cases"]]
elif role == "dispatch":
    rows = [{"id": case["id"], "bay": choose_bay(case)} for case in view["cases"]]
else:
    raise ValueError(f"Unexpected seat role: {role!r}")

request = Request(
    f"{base}/submit",
    method="POST",
    headers={"Content-Type": "application/json"},
    data=json.dumps({"rows": rows}, separators=(",", ":")).encode("utf-8"),
)
with http.open(request, timeout=5) as response:
    if json.load(response) != {"accepted": True}:
        raise ValueError("World did not accept the submission")
print(f"{role} submitted {len(rows)} rows")
PY
