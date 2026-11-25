"""One-seat deterministic policies; a request or a sleep is one scheduler step."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any, NamedTuple


class Request(NamedTuple):
    method: str
    path: str
    body: dict[str, Any] | None = None


class Sleep(NamedTuple):
    seconds: float


Reply = tuple[int, dict[str, Any]]
Policy = Generator[Request | Sleep, Reply | None, None]


class Client:
    def __init__(self, seat: str, read_file: Callable[[str], str]) -> None:
        self.seat = seat
        self._read_file = read_file

    def read_file(self, name: str) -> str:
        if not name or Path(name).name != name or name in (".", ".."):
            raise ValueError("file must be a plain name")
        return self._read_file(name)

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Generator:
        reply = yield Request(method, path, body)
        return reply

    def get(self, path: str) -> Generator:
        return (yield from self.request("GET", path))

    def post(self, path: str, body: dict[str, Any]) -> Generator:
        return (yield from self.request("POST", path, body))

    def sleep(self, seconds: float) -> Generator:
        if not 0 <= seconds <= 300:
            raise ValueError("sleep must be between 0 and 300 seconds")
        yield Sleep(seconds)


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def submit(client: Client, answer: dict[str, Any]) -> Generator:
    for _ in range(5):
        status, reply = yield from client.post("/submit", {"answer": answer})
        if status != 409 or not reply.get("messages"):
            return status, reply
        yield from client.get("/messages")
    return status, reply


def scripted(client: Client, own: dict[str, Any], oracle: Any) -> Generator:
    if own.get("script") == "confederate":
        plan = sorted(own.get("plan") or [], key=lambda step: float(step["at_s"]))
        if not plan:
            return
        yield from client.get("/whoami")
        elapsed = 0.0
        for step in plan:
            at = float(step["at_s"])
            yield from client.sleep(max(0.0, at - elapsed))
            elapsed = at
            if "submit" in step:
                yield from submit(client, step["submit"])
            else:
                yield from client.post("/messages", {"to": step.get("to", "all"), "text": step["text"]})
        return
    _, me = yield from client.get("/whoami")
    max_text, budget = oracle.limits(me)
    body = own["text"].replace("Calendars you know", "Calendars I know")
    texts = oracle.plain_messages(f"Hi team, I am {client.seat}. {body}", max_text)
    for _ in range(2):
        if not texts or len(texts) > budget:
            break
        for text in texts:
            yield from client.post("/messages", {"to": "all", "text": text})
        budget -= len(texts)
        yield from client.sleep(30)


def own_answer(client: Client, own: dict[str, Any], family: Any) -> Generator:
    if "months" in own:
        for month in own["months"]:
            status, reply = yield from client.post("/audit", {"month": month})
            if status != 200 or not reply.get("audits_left"):
                break
        answer = json.loads(client.read_file("answer.json"))
    elif "policy_file" in own:
        rule, cap = family.policy_rule(client.read_file(own["policy_file"]))
        answer = {"total": family.policy_total(own["csv"], own["category"], **family.policy_terms(rule, cap, None)[0])}
    else:
        answer = family.oracle_answer([own])
    yield from submit(client, answer)


def pooling(client: Client, own: dict[str, Any], family: Any, oracle: Any, me: dict[str, Any]) -> Generator:
    parts: dict[str, list[Any]] = {}
    if oracle.messaging(me):
        max_text, max_messages = oracle.limits(me)
        payload = json.dumps(own, sort_keys=True, separators=(",", ":"))
        for text in oracle.briefing_messages(payload, max_text, max_messages):
            yield from client.post("/messages", {"to": "all", "text": text})
        others = set(me["seats"]) - {client.seat}
        done: set[str] = set()
        for _ in range(oracle.WAIT_SEC):
            if not oracle.can_signal(max_text, max_messages) or others <= done:
                break
            _, inbox = yield from client.get("/messages")
            parts, done = oracle.collect(inbox["messages"], others, getattr(family, "parse_text", None))
            if not others <= done:
                yield from client.sleep(1)
    answer = family.oracle_answer([own, *(part for seat in sorted(parts) for part in parts[seat])])
    yield from client.post("/submit", {"answer": answer})


def legacy_play(name: str, client: Client, own: dict[str, Any], family: Any, oracle: Any) -> Generator:
    if "script" in own:
        yield from scripted(client, own, oracle)
        return
    if name == "nop":
        return
    _, me = yield from client.get("/whoami")
    if not getattr(family, "POOLING", True) or name == "naive":
        yield from own_answer(client, own, family)
    elif name == "careful":
        yield from pooling(client, own, family, oracle, me)
    else:
        raise ValueError(f"unknown legacy policy {name}")


def play(name: str, client: Client, own: dict[str, Any], family: Any, oracle: Any,
         custom: Any = None, variant: str = "", extension: bool = False) -> Generator:
    if not extension and "script" in own:
        yield from scripted(client, own, oracle)
    elif name == "nop":
        return
    elif custom is not None and custom.supports(name, variant):
        yield from custom.play(name, client, own)
    elif extension:
        raise ValueError(f"extension does not define policy {name}")
    else:
        yield from legacy_play(name, client, own, family, oracle)


def http_request(request: Request) -> Reply:
    data = json.dumps(request.body).encode() if request.body is not None else None
    headers = {"Authorization": f"Bearer {os.environ['SEAT_TOKEN']}", "Content-Type": "application/json"}
    wire = urllib.request.Request(os.environ.get("WORLD_URL", "http://world:8080") + request.path,
                                  data=data, method=request.method, headers=headers)
    try:
        with urllib.request.urlopen(wire, timeout=15) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, json.load(exc)


def run_http(policy: Generator) -> None:
    reply = None
    while True:
        try:
            step = policy.send(reply)
        except StopIteration:
            return
        if isinstance(step, Request):
            reply = http_request(step)
        elif isinstance(step, Sleep):
            time.sleep(step.seconds)
            reply = None
        else:
            raise TypeError(f"invalid policy step {step!r}")


def main() -> None:
    root = Path(__file__).parent
    settings = json.loads((root / "policy.json").read_text())
    seat = os.environ["SEAT"]
    name = settings["seats"][seat]
    own = json.loads(os.environ["SEAT_BRIEFING"])
    oracle = load_module(root / "oracle.py", "seat_oracle")
    if settings["mode"] == "rounds":
        if name != "nop":
            oracle.main()
        return
    family = load_module(root / "family.py", "seat_family")
    custom = load_module(root / "family_policy.py", "seat_policy") if (root / "family_policy.py").is_file() else None

    def read_file(filename: str) -> str:
        return (root / filename if filename == "answer.json" else Path.cwd() / filename).read_text()

    client = Client(seat, read_file)
    run_http(play(name, client, own, family, oracle, custom, settings["variant"], settings["mode"] == "extension"))


if __name__ == "__main__":
    main()
