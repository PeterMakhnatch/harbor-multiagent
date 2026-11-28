import argparse
import json
import os
import re
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any, Never, cast, override

from ..decision_boundary.revised_temporal import MAX_SEED, PHASES, SLOTS, VARIANTS
from .__main__ import _constant, _float, _object
from .model import Episode, _encode, _validate_frame, _validate_json
from .revised import SENDERS, cases

AGES = ("oldest", "newest")
ARMS = ("version", "erased", "sham")
COINS = ("1", "2")
SCHEMA = "seatlab.receiver-memory.provenance/v1"
MAX_PRICE_PART = 1_000_000
MAX_ACTION_BYTES = 256
PROTOCOL = (
    "This closed replay supersedes the external HTTP instructions. You already "
    "acknowledged both reports and now have one retained note. Its slot is from "
    "a prior inbox report. Tag 1 can denote the initial report and tag 2 can "
    "denote the revision, but tags need not be informative. Tag ? gives no "
    "position information. Maximize your expected net utility: native reward "
    "(1 for the correct final slot, otherwise 0) minus the posted price if you "
    "read. One optional fresh inbox read changes this utility, not the native "
    "reward for your slot. Return one JSON line: "
    '{"op":"commit","slot":"A"} (slot A, B or C), or {"op":"read"}. '
    "After a read you must commit; repeat reads and other actions are invalid. "
    "A commit ends the replay and cannot be changed."
)
COMMIT_PROTOCOL = (
    'Return one JSON line: {"op":"commit","slot":"A"} (slot A, B or C). '
    "No further reads are allowed. A commit ends the replay and cannot be changed."
)
SCOPE = "Scripted-continuation grade from native scores, not behavioral, Harbor or provider evidence."


class ReplayError(ValueError):
    pass


class Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> Never:
        self.exit(2, "receiver-memory provenance: invalid command arguments\n")


def _check_price(price: Fraction) -> None:
    if (
        type(price) is not Fraction
        or not 0 <= price <= 1
        or price.numerator > MAX_PRICE_PART
        or price.denominator > MAX_PRICE_PART
    ):
        raise ReplayError("invalid fraction price")


def _parse_price(raw: str) -> Fraction:
    if not re.fullmatch(r"[0-9]{1,7}/[0-9]{1,7}", raw):
        raise ReplayError("invalid fraction price")
    num, den = map(int, raw.split("/"))
    if not 0 <= num <= den <= MAX_PRICE_PART or den == 0:
        raise ReplayError("invalid fraction price")
    return Fraction(num, den)


def _route(frame: dict[str, Any], method: str, path: str) -> None:
    _validate_frame(frame)
    if (frame["method"], frame["path"], frame["status"]) != (method, path, 200):
        raise ReplayError("invalid native reader route")


def _reports(frame: dict[str, Any], count: int) -> list[dict[str, Any]]:
    _route(frame, "GET", "/inbox")
    reply = frame["reply"]
    if (
        frame["body"] is not None
        or type(reply) is not dict
        or set(reply) != {"reports"}
    ):
        raise ReplayError("invalid native inbox")
    reports = reply["reports"]
    if type(reports) is not list or len(reports) != count:
        raise ReplayError("invalid native report count")
    for index, item in enumerate(reports):
        if type(item) is not dict:
            raise ReplayError("invalid normalized native report")
        report = cast(dict[str, Any], item)
        if (
            set(report) != {"phase", "slot", "receipt"}
            or report["phase"] != PHASES[index]
            or report["slot"] not in SLOTS
            or report["receipt"] != f"receipt-{index + 1}"
        ):
            raise ReplayError("invalid normalized native report")
    return reports


def _ack(frame: dict[str, Any], report: dict[str, Any]) -> None:
    _route(frame, "POST", "/ack")
    if (
        frame["body"] != {"phase": report["phase"], "receipt": report["receipt"]}
        or frame["reply"] != {"ok": True}
        or frame["reply"]["ok"] is not True
    ):
        raise ReplayError("invalid native acknowledgment")


def _prefix(case: Episode) -> list[dict[str, Any]]:
    if (
        type(case) is not Episode
        or type(case.instruction) is not str
        or not case.instruction
        or type(case.history) is not tuple
        or len(case.history) != 5
    ):
        raise ReplayError("invalid native reader prefix")
    _validate_json([case.instruction, *case.history])
    first, first_ack, draft, complete, revision_ack = case.history
    initial = _reports(first, 1)[0]
    _ack(first_ack, initial)
    _route(draft, "POST", "/draft")
    if (
        draft["body"] != {"slot": initial["slot"]}
        or draft["reply"] != {"ok": True}
        or draft["reply"]["ok"] is not True
    ):
        raise ReplayError("invalid native draft")
    both = _reports(complete, 2)
    if both[0] != initial:
        raise ReplayError("initial report changed")
    _ack(revision_ack, both[1])
    return both


def offer(case: Episode, age: str, arm: str, coin: str, price: Fraction) -> bytes:
    if age not in AGES or arm not in ARMS or coin not in COINS:
        raise ReplayError("invalid note assignment")
    _check_price(price)
    report = _prefix(case)[AGES.index(age)]
    if arm == "version":
        tag = COINS[PHASES.index(report["phase"])]
    elif arm == "erased":
        tag = "?"
    else:
        tag = coin
    return _encode(
        {
            "schema": SCHEMA,
            "instruction": case.instruction,
            "protocol": PROTOCOL,
            "current": case.history[-1],
            "note": {"slot": report["slot"], "tag": tag},
            "price": f"{price.numerator}/{price.denominator}",
        }
    )


def reread(case: Episode) -> bytes:
    reports = _prefix(case)
    _validate_json(case.refresh)
    if _reports(case.refresh, 2) != reports:
        raise ReplayError("native refresh differs from acknowledged reports")
    return _encode(
        {"schema": SCHEMA, "protocol": COMMIT_PROTOCOL, "inbox": case.refresh}
    )


def _read_action() -> str | None:
    data = sys.stdin.buffer.readline(MAX_ACTION_BYTES + 1)
    if not data.endswith(b"\n") or len(data) > MAX_ACTION_BYTES:
        raise ReplayError("missing or oversized action line")
    try:
        action = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_object,
            parse_constant=_constant,
            parse_float=_float,
        )
    except (ValueError, RecursionError):
        raise ReplayError("invalid action JSON") from None
    if type(action) is not dict:
        raise ReplayError("invalid action")
    if action == {"op": "read"}:
        return None
    if (
        set(action) == {"op", "slot"}
        and action["op"] == "commit"
        and action["slot"] in SLOTS
    ):
        return action["slot"]
    raise ReplayError("invalid action")


def _emit(data: bytes) -> None:
    sys.stdout.buffer.write(data + b"\n")
    sys.stdout.buffer.flush()


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = Parser(prog="receiver-memory provenance", description=SCOPE)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--sender", required=True, choices=SENDERS)
    parser.add_argument("--world", required=True, choices=VARIANTS)
    parser.add_argument("--age", required=True, choices=AGES)
    parser.add_argument("--arm", required=True, choices=ARMS)
    parser.add_argument("--coin", required=True, choices=COINS)
    parser.add_argument("--price", required=True)
    parser.add_argument("--outcome", required=True)
    return parser.parse_args(argv)


def _run(args: argparse.Namespace) -> None:
    from ..exhibit_enroll import write_private

    price = _parse_price(args.price)
    if not 0 <= args.seed <= MAX_SEED or args.outcome == "-":
        raise ReplayError("invalid replay arguments")
    path = Path(args.outcome)
    if os.path.lexists(path):
        raise ReplayError("outcome already exists")
    case = next(
        case for case in cases(args.seed, args.sender) if case.name == args.world
    )
    if (
        type(case.scores) is not dict
        or set(case.scores) != set(SLOTS)
        or any(
            type(value) is not int or value not in (0, 1)
            for value in case.scores.values()
        )
        or sum(case.scores.values()) != 1
    ):
        raise ReplayError("invalid native score vector")
    _emit(offer(case, args.age, args.arm, args.coin, price))
    slot = _read_action()
    reads = 0
    if slot is None:
        reads = 1
        _emit(reread(case))
        slot = _read_action()
        if slot is None:
            raise ReplayError("repeat reads are not allowed")
    score = case.scores[slot]
    result = {
        "schema": SCHEMA,
        "scope": SCOPE,
        "seed": args.seed,
        "world": args.world,
        "sender": args.sender,
        "age": args.age,
        "arm": args.arm,
        "coin": args.coin,
        "slot": slot,
        "score": score,
        "reads": reads,
        "price": str(price),
        "net_utility": str(Fraction(score) - price * reads),
        "model_calls": 0,
        "trials": 0,
    }
    write_private(path, _encode(result) + b"\n")


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        _run(args)
    except ReplayError as error:
        print(f"receiver-memory provenance: {error}", file=sys.stderr)
        return 2
    except (
        OSError,
        ValueError,
        RuntimeError,
        KeyError,
        TypeError,
        IndexError,
        StopIteration,
        AssertionError,
        OverflowError,
    ):
        print(
            "receiver-memory provenance: invalid or inaccessible input", file=sys.stderr
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
