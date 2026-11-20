"""Measure and replay the registered private calendar turns."""

import argparse
import json
import os
import re
import sqlite3
import sys
from collections.abc import Generator, Mapping
from contextlib import ExitStack, contextmanager
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

from . import calendar_packet, calendar_protocol, deepseek, spend
from . import clarify_run as private_io
from .calendar_packet import World
from .calendar_protocol import Block

_SCHEMA = "seatlab.calendar-turn/v1"
_BLOCKS = {block.id: block for block in calendar_protocol.blocks()}
_BRANCHES = {f"{block.id}-{arm}" for block in _BLOCKS.values() for arm in block.arms}
_INITIAL = (("p1", "cal_initial_p1"), ("p2", "cal_initial_p2"))
_INITIAL_ROLES = {role for _, role in _INITIAL}
_COUNTS = ("input_tokens", "cache_tokens", "output_tokens")
_NO_RESPONSE = ("response_sha256", "response_text", "value")
_MAX_RECEIPT_BYTES = 6 * deepseek._MAX_RESPONSE_BYTES + 4096
_USD = re.compile(r"[0-9]+\.[0-9]{9}")


class TurnReceipt(TypedDict):
    schema: str
    run: str
    role: str
    prompt_sha256: str
    status: Literal["accepted", "invalid", "failed"]
    reason: str | None
    served_model: str | None
    input_tokens: int | None
    cache_tokens: int | None
    output_tokens: int | None
    response_sha256: str | None
    response_text: str | None
    value: list[int] | int | None


class TurnError(ValueError):
    """A safe replay or setup diagnostic, without provider text or paths."""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


class SeedPairError(TurnError):
    """The independently verified seats and the reason each other seat failed."""

    def __init__(
        self, issues: dict[str, str], values: dict[str, tuple[int, ...]]
    ) -> None:
        self.issues = issues
        self.values = values
        super().__init__("seed_pair_incomplete")


def _identity(run_id: str, role: str) -> None:
    if (
        not isinstance(run_id, str)
        or not isinstance(role, str)
        or not (
            (role in _INITIAL_ROLES and run_id in _BLOCKS)
            or (role in {"cal_recheck", "cal_decide"} and run_id in _BRANCHES)
        )
    ):
        raise TurnError("invalid_call_identity")


def _digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _decode(text: str, role: str) -> tuple[int, ...] | int | None:
    try:
        if role == "cal_decide":
            return calendar_protocol.decode_slot(text)
        return calendar_protocol.decode_list(text)
    except (ValueError, RecursionError):
        return None


def _json_value(value: tuple[int, ...] | int | None) -> list[int] | int | None:
    return list(value) if isinstance(value, tuple) else value


def _receipt(prompt: str, run_id: str, role: str) -> TurnReceipt:
    _identity(run_id, role)
    try:
        if not isinstance(prompt, str) or not prompt:
            raise TurnError("prompt_invalid")
        prompt_sha256 = _digest(prompt)
    except UnicodeError:
        raise TurnError("prompt_invalid") from None
    return {
        "schema": _SCHEMA,
        "run": run_id,
        "role": role,
        "prompt_sha256": prompt_sha256,
        "status": "failed",
        "reason": None,
        "served_model": None,
        "input_tokens": None,
        "cache_tokens": None,
        "output_tokens": None,
        "response_sha256": None,
        "response_text": None,
        "value": None,
    }


def measure(prompt: str, run_id: str, role: str) -> TurnReceipt:
    """Make one direct turn; retain response text and usage only for private replay."""
    receipt = _receipt(prompt, run_id, role)
    try:
        result = deepseek.turn(prompt, run_id=run_id, role=role)
    except deepseek.SeatUnavailable as error:
        receipt["reason"] = error.category
        if error.settled_usage is not None:
            total, hit, output = error.settled_usage
            receipt["input_tokens"] = total
            receipt["cache_tokens"] = hit
            receipt["output_tokens"] = output
        return receipt
    except (spend.SpendRefused, sqlite3.Error):
        receipt["reason"] = "ledger_unavailable"
        return receipt
    except OSError:
        receipt["reason"] = "io_unavailable"
        return receipt

    receipt["served_model"] = result.served_model
    receipt["input_tokens"] = result.input_tokens
    receipt["cache_tokens"] = result.cache_tokens
    receipt["output_tokens"] = result.output_tokens
    try:
        response_sha256 = _digest(result.text)
    except UnicodeError:
        receipt["reason"] = "provider_response_invalid"
        return receipt
    value = _decode(result.text, role)
    receipt["status"] = "invalid" if value is None else "accepted"
    receipt["reason"] = "action_invalid" if value is None else None
    receipt["response_text"] = result.text
    receipt["response_sha256"] = response_sha256
    receipt["value"] = _json_value(value)
    return receipt


def _ledger_row(
    ledger: Mapping[str, object], run_id: str, role: str
) -> dict[str, object] | None:
    if (
        not isinstance(ledger, dict)
        or ledger.get("schema") != "seatlab.direct-flash-cost/v1"
        or ledger.get("currency") != "USD"
        or not isinstance(calls := ledger.get("calls"), list)
    ):
        raise TurnError("ledger_invalid")
    match: dict[str, object] | None = None
    for row in calls:
        if not isinstance(row, dict):
            raise TurnError("ledger_invalid")
        if (row.get("run"), row.get("role")) == (run_id, role):
            if match is not None:
                raise TurnError("ledger_ambiguous")
            if not all(isinstance(key, str) for key in row):
                raise TurnError("ledger_invalid")
            match = cast(dict[str, object], row)
    return match


def _count(value: object) -> bool:
    return type(value) is int and value >= 0


def _join_usage(
    receipt: Mapping[str, object], row: dict[str, object] | None, *, require_model: bool
) -> None:
    total, hit, output = (receipt[key] for key in _COUNTS)
    if (
        type(total) is not int
        or type(hit) is not int
        or type(output) is not int
        or not 0 <= hit <= total
        or output < 0
    ):
        raise TurnError("receipt_invalid")
    if row is None:
        raise TurnError("ledger_missing")
    low, high = row.get("estimated_usd_low"), row.get("estimated_usd_high")
    if (
        low is None
        and high is None
        and row.get("unsettled_hold_usd") == spend._usd(spend._HOLD)
    ):
        raise TurnError("ledger_unsettled")
    model = row.get("served_model")
    try:
        deepseek._served_model(model)
    except deepseek.SeatUnavailable:
        raise TurnError("ledger_invalid") from None
    if (
        not isinstance(low, str)
        or not isinstance(high, str)
        or not _USD.fullmatch(low)
        or not _USD.fullmatch(high)
        or Decimal(low) > Decimal(high)
        or row.get("unsettled_hold_usd") != spend._usd(0)
        or not all(
            _count(row.get(key)) for key in ("cache_hit", "cache_miss", "output")
        )
    ):
        raise TurnError("ledger_invalid")
    receipt_model = receipt["served_model"]
    if ((require_model or receipt_model is not None) and receipt_model != model) or (
        row["cache_hit"],
        row["cache_miss"],
        row["output"],
    ) != (hit, total - hit, output):
        raise TurnError("ledger_mismatch")


def verify_turn(
    receipt: Mapping[str, object],
    prompt: str,
    run_id: str,
    role: str,
    ledger: Mapping[str, object],
) -> tuple[int, ...] | int:
    """Replay a private turn against the scoped prompt and the original settled usage."""
    expected = _receipt(prompt, run_id, role)
    if (
        not isinstance(receipt, dict)
        or receipt.keys() != expected.keys()
        or any(receipt[key] != expected[key] for key in ("schema", "run", "role"))
    ):
        raise TurnError("receipt_invalid")
    if receipt["prompt_sha256"] != expected["prompt_sha256"]:
        raise TurnError("prompt_mismatch")
    status, reason = receipt["status"], receipt["reason"]
    row = _ledger_row(ledger, run_id, role)
    if status == "failed":
        if (
            not isinstance(reason, str)
            or not reason
            or any(receipt[key] is not None for key in _NO_RESPONSE)
        ):
            raise TurnError("receipt_invalid")
        if any(receipt[key] is not None for key in _COUNTS):
            if reason != "provider_response_invalid":
                raise TurnError("receipt_invalid")
            _join_usage(receipt, row, require_model=False)
        elif receipt["served_model"] is not None:
            raise TurnError("receipt_invalid")
        elif row is not None and row.get("estimated_usd_high") is not None:
            raise TurnError("ledger_mismatch")
        raise TurnError("turn_failed")

    text = receipt["response_text"]
    if status not in ("accepted", "invalid") or not isinstance(text, str):
        raise TurnError("receipt_invalid")
    try:
        if receipt["response_sha256"] != _digest(text):
            raise TurnError("response_mismatch")
    except UnicodeError:
        raise TurnError("receipt_invalid") from None
    value = _decode(text, role)
    stored = receipt["value"]
    if value is None:
        if status != "invalid" or reason != "action_invalid" or stored is not None:
            raise TurnError("receipt_invalid")
    elif (
        status != "accepted"
        or reason is not None
        or (
            isinstance(value, tuple)
            and (
                not isinstance(stored, list)
                or any(type(item) is not int for item in stored)
            )
        )
        or (isinstance(value, int) and type(stored) is not int)
        or stored != _json_value(value)
    ):
        raise TurnError("receipt_invalid")
    _join_usage(receipt, row, require_model=True)
    if value is None:
        raise TurnError("model_invalid")
    return value


def _inputs(block: Block, world: World) -> tuple[tuple[str, str, str], ...]:
    if (
        any(
            type(n) is not int for n in (block.world, block.direction, block.repetition)
        )
        or _BLOCKS.get(block.id) != block
        or type(world.index) is not int
        or world.index != block.world
    ):
        raise TurnError("invalid_call_identity")
    return tuple(
        (seat, role, calendar_protocol.initial_prompt(table))
        for (seat, role), table in zip(_INITIAL, world.tables, strict=True)
    )


@contextmanager
def _seed_directory(
    study: Path, block: Block, *, claim: bool = False
) -> Generator[int]:
    with ExitStack() as stack:
        directory = stack.enter_context(
            private_io._directory(private_io._study_path(study))
        )
        private_io._private(directory, mode=0o700)
        for name in ("seeds", block.id):
            if claim:
                try:
                    os.mkdir(name, mode=0o700, dir_fd=directory)
                except FileExistsError:
                    if name == block.id:
                        raise TurnError("seed_already_attempted") from None
            directory = os.open(name, private_io._DIRECTORY, dir_fd=directory)
            stack.callback(os.close, directory)
            private_io._private(directory, mode=0o700)
        yield directory


def _report(spend_dir: Path, block: Block) -> dict[str, Any]:
    try:
        return spend.report(private_io._absolute(spend_dir), run_prefix=block.id)
    except (OSError, sqlite3.Error, ValueError, TypeError, ArithmeticError):
        raise TurnError("ledger_unavailable") from None


def load_seed_pair(
    study: Path, block: Block, world: World, spend_dir: Path
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Check each seat independently, even if the other seat never produced a receipt."""
    inputs = _inputs(block, world)
    ledger = _report(spend_dir, block)
    issues: dict[str, str] = {}
    values: dict[str, tuple[int, ...]] = {}
    try:
        with _seed_directory(study, block) as directory:
            for seat, role, prompt in inputs:
                try:
                    with private_io._regular(
                        directory, f"{seat}.json", 0o600
                    ) as stream:
                        raw = stream.read(_MAX_RECEIPT_BYTES + 1)
                    if len(raw) > _MAX_RECEIPT_BYTES:
                        raise TurnError("receipt_invalid")
                    value = verify_turn(
                        private_io._object(raw), prompt, block.id, role, ledger
                    )
                    if not isinstance(value, tuple):
                        raise TurnError("receipt_invalid")
                    values[seat] = value
                except TurnError as error:
                    issues[seat] = error.category
                except FileNotFoundError:
                    issues[seat] = "receipt_missing"
                except (OSError, ValueError, RecursionError):
                    issues[seat] = "receipt_invalid"
    except FileNotFoundError:
        issues = dict.fromkeys((seat for seat, _ in _INITIAL), "receipt_missing")
    except (OSError, ValueError):
        issues = dict.fromkeys((seat for seat, _ in _INITIAL), "receipt_invalid")
    if issues:
        raise SeedPairError(issues, values)
    return values["p1"], values["p2"]


def _seed(study: Path, block: Block, spend_dir: Path) -> dict[str, str]:
    world = calendar_packet.load(calendar_packet.PACKET, calendar_packet.PACKET_SHA256)[
        block.world
    ]
    inputs = _inputs(block, world)
    ledger = _report(spend_dir, block)
    if any(_ledger_row(ledger, block.id, role) is not None for _, role, _ in inputs):
        raise TurnError("seed_already_attempted")
    issues = {}
    with _seed_directory(study, block, claim=True) as directory:
        os.environ["SEATLAB_SPEND_DIR"] = str(private_io._absolute(spend_dir))
        spend.bind_host_spend_dir()
        deepseek.bind_host_key()
        for seat, role, prompt in inputs:
            receipt = measure(prompt, block.id, role)
            private_io._publish(directory, f"{seat}.json", receipt, mode=0o600)
            try:
                verify_turn(receipt, prompt, block.id, role, _report(spend_dir, block))
            except TurnError as error:
                issues[seat] = error.category
                if error.category != "model_invalid":
                    break
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    seed = commands.add_parser("seed")
    seed.add_argument("--study", type=Path, required=True)
    seed.add_argument("--block", required=True)
    seed.add_argument("--spend-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        block = _BLOCKS.get(args.block)
        if block is None:
            raise TurnError("invalid_call_identity")
        issues = _seed(args.study, block, args.spend_dir)
    except TurnError as error:
        print(json.dumps({"error": error.category}), file=sys.stderr)
        return 1
    except OSError:
        print(json.dumps({"error": "study_io_failed"}), file=sys.stderr)
        return 1
    except (ValueError, RuntimeError):
        print(json.dumps({"error": "seed_setup_failed"}), file=sys.stderr)
        return 1
    print(json.dumps({"block": block.id, "issues": issues}, sort_keys=True))
    return int(bool(issues))


if __name__ == "__main__":
    raise SystemExit(main())
