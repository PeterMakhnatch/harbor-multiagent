import json
import os
import time
from http import HTTPStatus
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

WORLD = "http://world:8080"
OPENER = build_opener(ProxyHandler({}))
CASE_COUNT = 24
REQUEST_TIMEOUT = 5.0
BUILDER_TIMEOUT = 60.0


def get_json(path, timeout=REQUEST_TIMEOUT):
    with OPENER.open(f"{WORLD}{path}", timeout=timeout) as response:
        return json.load(response)


def post_json(path, payload):
    request = Request(
        f"{WORLD}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with OPENER.open(request, timeout=REQUEST_TIMEOUT) as response:
        response.read()


def wait_for_inboxes():
    deadline = time.monotonic() + BUILDER_TIMEOUT
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            return get_json("/builder", timeout=min(REQUEST_TIMEOUT, remaining))
        except HTTPError as error:
            if error.code != HTTPStatus.CONFLICT:
                raise
            error.close()
        time.sleep(max(0.0, min(1.0, deadline - time.monotonic())))
    raise TimeoutError("The reviews did not arrive before the builder deadline")


def choose_kit(inbox, choices):
    left, right = inbox["left"], inbox["right"]
    if left is None or right is None:
        return "K1"
    shared = [kit for kit in choices if kit in left and kit in right]
    return shared[0] if len(shared) == 1 else "K1"


def main():
    seat = os.environ["SEAT"]
    if seat in ("left", "right"):
        path = f"/review/{seat}"
        reviews = get_json(path)
        if len(reviews["allowed"]) != CASE_COUNT:
            raise ValueError(f"Expected {CASE_COUNT} reviews")
        post_json(path, reviews)
        print(f"{seat} submitted {CASE_COUNT} reviews")
    elif seat == "builder":
        batch = wait_for_inboxes()
        if len(batch["inboxes"]) != CASE_COUNT:
            raise ValueError(f"Expected {CASE_COUNT} inboxes")
        kits = [choose_kit(inbox, batch["choices"]) for inbox in batch["inboxes"]]
        post_json("/builder", {"kits": kits})
        print(f"builder submitted {CASE_COUNT} kits")
    else:
        raise ValueError(f"Unknown seat: {seat!r}")


if __name__ == "__main__":
    main()
