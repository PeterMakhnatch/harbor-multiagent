from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Hashable, Iterable
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

Row = dict[str, Any]
LIMIT = 1 << 20
FLASH = {"deepseek-flash", "deepseek-v4.1-flash-1017"}


def check(success: bool, label: str) -> None:
    if not success:
        raise ValueError(label)


def read(root: Path, basename: str, sha256hex: str) -> Row:
    path = root / basename
    check(path.name == basename and not path.is_symlink() and path.is_file(), f"not a released regular file: {basename}")
    with path.open("rb") as handle:
        data = handle.read(LIMIT + 1)
    check(0 < len(data) <= LIMIT and hashlib.sha256(data).hexdigest() == sha256hex,
          f"released bytes differ in {basename}")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise TypeError(f"expected an object in {basename}")
    return value


def index(rows: Iterable[Row], key: str | Callable[[Row], Hashable], label: str) -> dict[Any, Row]:
    result: dict[Any, Row] = {}
    for row in rows:
        value = row[key] if isinstance(key, str) else key(row)
        check(value not in result, f"duplicate {label}: {value}")
        result[value] = row
    return result


def settled(call: Row, role: str) -> str:
    check(call.get("role") == role, f"wrong role for {call.get('run')}: expected {role}")
    try:
        hold = Decimal(str(call.get("unsettled_hold_usd")))
    except InvalidOperation as exc:
        raise ValueError(f"invalid settlement for {call.get('run')}") from exc
    check(hold.is_finite() and hold == 0, f"unsettled call: {call.get('run')}")
    for field in ("cache_hit", "cache_miss", "output"):
        value = call.get(field)
        check(type(value) is int and value >= 0, f"bad {field} count for {call.get('run')}")
    model = call.get("served_model")
    if not isinstance(model, str) or model not in FLASH:
        raise ValueError(f"unknown served model in {call.get('run')}")
    return model
