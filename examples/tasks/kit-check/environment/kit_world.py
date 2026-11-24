"""Serve the deterministic kit-check control world."""

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from itertools import permutations
from pathlib import Path
from typing import TypedDict, cast

KITS = ("K1", "K2", "K3", "K4")
REVIEW_ROUTES = {"/review/left": "left", "/review/right": "right"}
SNAPSHOT_PATH = Path("/logs/world/kit-check.json")
MAX_BODY_BYTES = 4096

type ReviewBatch = list[list[str]]


class Inbox(TypedDict):
    left: list[str] | None
    right: list[str] | None


def fixture_reviews() -> dict[str, ReviewBatch]:
    left, right = [], []
    for gold, left_alternative, right_alternative in permutations(KITS, 3):
        left.append(sorted((gold, left_alternative)))
        right.append(sorted((gold, right_alternative)))
    return {"left": left, "right": right}


def _is_kit(value: object) -> bool:
    return isinstance(value, str) and value in KITS


def _is_review_pair(value: object) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(_is_kit(kit) for kit in value)
        and value[0] != value[1]
    )


class KitServer(HTTPServer):
    def __init__(self, address: tuple[str, int], mode: str):
        self.mode = mode
        self.original_reviews = fixture_reviews()
        self.reviews: dict[str, ReviewBatch] = {}
        self.inboxes: list[Inbox] | None = None
        self.committed = False
        super().__init__(address, KitHandler)

    def builder_inboxes(self) -> list[Inbox]:
        left = self.reviews["left"]
        right = self.reviews["right"]
        return [
            {
                "left": left_pair if self.mode == "visible" else None,
                "right": right_pair if self.mode == "visible" else None,
            }
            for left_pair, right_pair in zip(left, right, strict=True)
        ]

    def commit(self, selections: list[str], inboxes: list[Inbox]) -> None:
        cases = []
        for seed, (choice, inbox) in enumerate(zip(selections, inboxes, strict=True)):
            (correct,) = set(self.original_reviews["left"][seed]).intersection(
                self.original_reviews["right"][seed]
            )
            cases.append(
                {
                    "seed": seed,
                    "reviews": {
                        "left": self.reviews["left"][seed],
                        "right": self.reviews["right"][seed],
                    },
                    "observation": {"choices": KITS, **inbox},
                    "choice": choice,
                    "correct": correct,
                    "success": choice == correct,
                }
            )

        snapshot = {"schema": "kit-check/v1", "mode": self.mode, "cases": cases}
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = SNAPSHOT_PATH.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(snapshot, separators=(",", ":")) + "\n", encoding="utf-8"
        )
        temporary.replace(SNAPSHOT_PATH)
        self.committed = True


class KitHandler(BaseHTTPRequestHandler):
    server: KitServer
    timeout = 5

    def _reply(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_batch(self, key: str) -> list[object]:
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise ValueError("a valid Content-Length is required") from None
        if not 0 < length <= MAX_BODY_BYTES:
            raise ValueError("invalid body length")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("incomplete request body")
        try:
            payload = json.loads(body)
        except (ValueError, RecursionError):
            raise ValueError("a JSON request body is required") from None
        size = len(self.server.original_reviews["left"])
        if not isinstance(payload, dict) or payload.keys() != {key}:
            raise ValueError(f"expected a JSON object containing only {key}")
        batch = payload[key]
        if not isinstance(batch, list) or len(batch) != size:
            raise ValueError(f"expected {size} entries in {key}")
        return batch

    def do_GET(self) -> None:
        role = REVIEW_ROUTES.get(self.path)
        if self.path == "/health":
            self._reply(HTTPStatus.OK, {"status": "ok"})
        elif role is not None:
            self._reply(HTTPStatus.OK, {"allowed": self.server.original_reviews[role]})
        elif self.path == "/builder":
            if len(self.server.reviews) != len(REVIEW_ROUTES):
                self._reply(HTTPStatus.CONFLICT, {"error": "both reviews are required"})
                return
            inboxes = self.server.inboxes
            if inboxes is None:
                inboxes = self.server.builder_inboxes()
            self._reply(HTTPStatus.OK, {"choices": KITS, "inboxes": inboxes})
            self.server.inboxes = inboxes
        else:
            self._reply(HTTPStatus.NOT_FOUND, {"error": "unknown path"})

    def do_POST(self) -> None:
        role = REVIEW_ROUTES.get(self.path)
        try:
            if role is not None:
                self._post_review(role)
            elif self.path == "/builder":
                self._post_builder()
            else:
                self._reply(HTTPStatus.NOT_FOUND, {"error": "unknown path"})
        except ValueError as error:
            self._reply(HTTPStatus.BAD_REQUEST, {"error": str(error)})

    def _post_review(self, role: str) -> None:
        if role in self.server.reviews:
            self._reply(HTTPStatus.CONFLICT, {"error": "review already recorded"})
            return
        batch = self._read_batch("allowed")
        if not all(_is_review_pair(pair) for pair in batch):
            raise ValueError("each review entry must contain two distinct kits")
        self.server.reviews[role] = cast(ReviewBatch, batch)
        self._reply(HTTPStatus.CREATED, {"status": "recorded"})

    def _post_builder(self) -> None:
        if self.server.committed:
            self._reply(HTTPStatus.CONFLICT, {"error": "builder already recorded"})
            return
        inboxes = self.server.inboxes
        if inboxes is None:
            self._reply(
                HTTPStatus.CONFLICT, {"error": "builder observation is required"}
            )
            return
        batch = self._read_batch("kits")
        if not all(_is_kit(kit) for kit in batch):
            raise ValueError("each builder entry must be a valid kit")
        try:
            self.server.commit(cast(list[str], batch), inboxes)
        except OSError:
            self.log_error("could not write kit-check snapshot")
            self._reply(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "could not commit"})
            return
        self._reply(HTTPStatus.CREATED, {"status": "recorded"})


def main() -> None:
    mode = os.environ.get("KIT_CHECK_MODE", "visible")
    if mode not in {"visible", "silent"}:
        raise SystemExit("KIT_CHECK_MODE must be visible or silent")
    with KitServer(("0.0.0.0", 8080), mode) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
