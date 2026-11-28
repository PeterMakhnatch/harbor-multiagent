import json
import re
import sys
from typing import Any, Never

POLICIES = ("never", "always", "by-tag")
SCHEMA = "seatlab.receiver-memory.provenance/v1"
SLOTS = ("A", "B", "C")
MAX_BYTES = 8192


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_number(_: str) -> Never:
    raise ValueError("invalid JSON number")


def _fields(value: Any, *names: str) -> None:
    if type(value) is not dict or set(value) != set(names):
        raise ValueError("invalid wire fields")


def _decode(data: bytes) -> dict[str, Any]:
    if type(data) is not bytes or not 0 < len(data) < MAX_BYTES:
        raise ValueError("invalid wire size")
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_object,
            parse_constant=_reject_number,
            parse_float=_reject_number,
        )
    except (ValueError, RecursionError):
        raise ValueError("invalid wire JSON") from None
    if type(value) is not dict:
        raise ValueError("invalid wire object")
    return value


def _frame(value: Any, method: str, path: str) -> None:
    _fields(value, "method", "path", "status", "body", "reply")
    if (
        (value["method"], value["path"]) != (method, path)
        or type(value["status"]) is not int
        or value["status"] != 200
    ):
        raise ValueError("invalid wire frame")


def _price(value: Any) -> None:
    if type(value) is not str or not re.fullmatch(r"[0-9]{1,7}/[0-9]{1,7}", value):
        raise ValueError("invalid price")
    numerator, denominator = map(int, value.split("/"))
    if not 0 <= numerator <= denominator <= 1_000_000 or denominator == 0:
        raise ValueError("invalid price")


def first(offer_bytes: bytes, policy: str) -> dict[str, str]:
    if policy not in POLICIES:
        raise ValueError("invalid receiver policy")
    offer = _decode(offer_bytes)
    _fields(offer, "schema", "instruction", "protocol", "current", "note", "price")
    if (
        offer["schema"] != SCHEMA
        or type(offer["protocol"]) is not str
        or not offer["protocol"]
        or type(offer["instruction"]) is not str
        or not offer["instruction"]
    ):
        raise ValueError("invalid offer")
    current = offer["current"]
    _frame(current, "POST", "/ack")
    _fields(current["body"], "phase", "receipt")
    _fields(current["reply"], "ok")
    if (
        current["body"] != {"phase": "revision", "receipt": "receipt-2"}
        or current["reply"]["ok"] is not True
    ):
        raise ValueError("invalid revision acknowledgment")
    note = offer["note"]
    _fields(note, "slot", "tag")
    if note["slot"] not in SLOTS or note["tag"] not in ("1", "2", "?"):
        raise ValueError("invalid retained note")
    _price(offer["price"])
    if policy == "always" or (policy == "by-tag" and note["tag"] != "2"):
        return {"op": "read"}
    return {"op": "commit", "slot": note["slot"]}


def second(reply_bytes: bytes) -> dict[str, str]:
    reply = _decode(reply_bytes)
    _fields(reply, "schema", "protocol", "inbox")
    if (
        reply["schema"] != SCHEMA
        or type(reply["protocol"]) is not str
        or not reply["protocol"]
    ):
        raise ValueError("invalid read reply")
    inbox = reply["inbox"]
    _frame(inbox, "GET", "/inbox")
    _fields(inbox["reply"], "reports")
    reports = inbox["reply"]["reports"]
    if inbox["body"] is not None or type(reports) is not list or len(reports) != 2:
        raise ValueError("invalid inbox reports")
    for index, phase in enumerate(("initial", "revision"), 1):
        report = reports[index - 1]
        _fields(report, "phase", "slot", "receipt")
        if (
            report["phase"] != phase
            or report["receipt"] != f"receipt-{index}"
            or report["slot"] not in SLOTS
        ):
            raise ValueError("invalid ordered report")
    return {"op": "commit", "slot": reports[1]["slot"]}


def _line() -> bytes:
    data = sys.stdin.buffer.readline(MAX_BYTES)
    if not data.endswith(b"\n") or len(data) >= MAX_BYTES:
        raise ValueError("invalid wire line")
    return data[:-1]


def _emit(action: dict[str, str]) -> None:
    print(json.dumps(action, separators=(",", ":")), flush=True)


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in POLICIES:
        print("receiver: invalid policy", file=sys.stderr)
        return 2
    try:
        action = first(_line(), sys.argv[1])
        _emit(action)
        if action == {"op": "read"}:
            _emit(second(_line()))
    except ValueError:
        print("receiver: invalid wire input", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
