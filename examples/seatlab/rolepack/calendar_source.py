import json
import os
import stat
import subprocess
import sys
from hashlib import sha256
from importlib import import_module
from pathlib import Path
from typing import Any, cast

_PUBLIC_REPORT = "flotilla-calendar-list-results.json"
_REPORT_SHA256 = "9085102e5b3043e44ac5987f55041a9e41461beacfee9b9a7dfa10a257842a24"
_MODULE_SHA256 = {
    "__init__": "427f30bfbef25aa6b57b94616d826d9fc688635e72fd41133e9d9fe81afda7f4",
    "core": "fb7d40ded422b33a6afc533fc85de5dd89b4f2972b1c085458d330d32e59fd4e",
    "calendar_packet": "d3481128c952df071b05b1b946e6f4e965ad9a55417f895520922b5600879d5b",
    "calendar_protocol": "ed40c6ceeec2bab729e9cbf0b8d834024ba1443c186e7ae0f3f7e9b59a91868c",
    "calendar_list_packet": "b56ca45dc210ca4de75c083db1ef1d48791aba314952649c5e572cc84eea8cd2",
    "calendar_list_protocol": "0ca3fdeb87a1822b254a42fae293c6e7ed31ad2c6d2a37a3a1e4d873f86efb83",
}
_PACKET_FILES = (
    "flotilla-calendar-packet.json",
    "flotilla-calendar-external-results.json",
    "flotilla-calendar-list-packet.json",
)
_CASES = {
    f"rx-w{world:02d}-d{direction}-r{repetition}-blind": (world, direction, repetition)
    for world in (0, 4, 8)
    for direction in range(2)
    for repetition in range(2)
}
_MAX_ACTIONS = 4 * len(_CASES)
_SOURCE_LIMIT = 64 * 1024
_REQUEST_LIMIT = 4096
_OUTPUT_LIMIT = 128 * 1024
type _Document = dict[str, Any]


def _physical(path: Path) -> Path:
    absolute = path.absolute()
    if (
        len(os.fsencode(absolute)) > 4096
        or ".." in absolute.parts
        or absolute.resolve(strict=True) != absolute
    ):
        raise ValueError("calendar source requires physical paths")
    return absolute


def _read(path: Path) -> bytes:
    _physical(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("calendar source requires an unaliased regular file")
        raw = handle.read(_SOURCE_LIMIT + 1)
    if not 0 < len(raw) <= _SOURCE_LIMIT:
        raise ValueError("invalid calendar source file size")
    return raw


def _pinned(path: Path, expected: str) -> bytes:
    raw = _read(path)
    if sha256(raw).hexdigest() != expected:
        raise ValueError("calendar source bytes differ from the publication")
    return raw


def _slot_json(slot: int) -> str:
    return json.dumps({"slot": slot}, separators=(",", ":"))


def _peer(row: _Document, grade: Any, case: Any, source_hash: str) -> _Document | None:
    status, slot, reward = row["status"], row["slot"], row["reward"]
    if status == "missing" and slot is None and reward is None:
        return None
    if (
        status == "model_invalid"
        and slot is None
        and type(reward) is int
        and reward == 0
    ):
        return None
    if (
        status != "complete"
        or type(slot) is not int
        or type(reward) is not int
        or grade(case, _slot_json(slot)) != reward
    ):
        raise ValueError("calendar prior action differs from the published score")
    return {
        "answer": {"slot": slot},
        "run": row["run"],
        "source_sha256": source_hash,
        "response_sha256": None,
    }


def _records(
    cases: dict[str, Any],
    stock: Any,
    rows: dict[str, _Document],
    legal: list[int],
    source_hash: str,
) -> list[_Document]:
    records = []
    for case_id, (world, direction, repetition) in sorted(_CASES.items()):
        case = cases[case_id]
        run = stock.Cell(case, 0, "rr").id
        row = rows[run]
        if (case.world, case.direction, case.repetition) != (
            world,
            direction,
            repetition,
        ):
            raise ValueError("calendar case coordinates differ")
        if (
            (row["case"], row["source_arm"], row["condition"])
            != (case_id, "blind", "rr")
            or any(
                type(row[key]) is not int
                for key in ("world", "direction", "repetition", "draw")
            )
            or (row["world"], row["direction"], row["repetition"], row["draw"])
            != (world, direction, repetition, 0)
        ):
            raise ValueError("calendar prior row does not match the registered case")
        prompt = stock.prompt(case, "rr")
        if sha256(prompt.encode("utf-8")).hexdigest() != case.prompt_sha256:
            raise ValueError("calendar replay prompt differs from the original receipt")
        optimal = [slot for slot in legal if stock.grade(case, _slot_json(slot)) == 1]
        records.append(
            {
                "id": f"cal-{case_id}",
                "task": "calendar",
                "world": f"w{world:02d}",
                "prompt": prompt,
                "prompt_sha256": case.prompt_sha256,
                "peer": _peer(row, stock.grade, case, source_hash),
                "rubric": {
                    "field": "slot",
                    "scalar": True,
                    "items": [{"key": "slot", "valid": legal, "optimal": optimal}],
                },
            }
        )
    return records


def _stock(root: Path, actions: list[tuple[str, int]]) -> _Document:
    seatlab = root / "examples" / "seatlab"
    results = seatlab / "results"
    raw = _pinned(results / _PUBLIC_REPORT, _REPORT_SHA256)
    for name, digest in _MODULE_SHA256.items():
        _pinned(seatlab / "visibility" / f"{name}.py", digest)
    for name in _PACKET_FILES:
        _read(results / name)
    sys.path.insert(0, str(root))
    # These modules belong to the pinned external task checkout, not this package.
    source = "examples.seatlab.visibility"
    calendar_packet = import_module(f"{source}.calendar_packet")
    calendar_list_packet = import_module(f"{source}.calendar_list_packet")
    calendar_list_protocol = import_module(f"{source}.calendar_list_protocol")
    calendar_protocol = import_module(f"{source}.calendar_protocol")

    report = calendar_packet._json(raw)
    cases = {
        case.id: case for case in calendar_list_packet.load() if case.arm == "blind"
    }
    if (
        cases.keys() != _CASES.keys()
        or report["schema"] != "seatlab.calendar-list-report/v1"
    ):
        raise ValueError("invalid registered calendar source")
    rows = {row["run"]: row for row in report["rows"]}
    if len(rows) != len(report["rows"]) or len(rows) != 192:
        raise ValueError("duplicate or absent calendar result rows")
    legal = [
        calendar_protocol._checked_slot(slot)
        for slot in range(calendar_protocol._SLOT_COUNT)
    ]
    records = _records(
        cases, calendar_list_protocol, rows, legal, sha256(raw).hexdigest()
    )
    scores = []
    for record_id, slot in actions:
        if not isinstance(record_id, str) or not record_id.startswith("cal-"):
            raise ValueError("invalid calendar record ID")
        case = cases[record_id.removeprefix("cal-")]
        scores.append(bool(calendar_list_protocol.grade(case, _slot_json(slot))))
    return {"records": records, "scores": scores}


def _worker(root: Path, actions: list[tuple[str, int]]) -> _Document:
    try:
        source = _physical(root)
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-B",
                "-X",
                f"pycache_prefix={os.devnull}",
                str(Path(__file__).resolve(strict=True)),
                str(source),
            ],
            cwd=source,
            env={},
            input=json.dumps(actions, separators=(",", ":")).encode("ascii"),
            capture_output=True,
            timeout=10,
            check=True,
        )
        if not 0 < len(completed.stdout) <= _OUTPUT_LIMIT:
            raise ValueError("invalid calendar stock worker output size")
        return json.loads(completed.stdout)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        raise ValueError("missing, altered, or unsafe public calendar source") from None


def export(root: Path) -> list[_Document]:
    return _worker(root, [])["records"]


def _completed(plan: list[_Document], rows: object) -> list[tuple[str, int, bool]]:
    if not isinstance(rows, list) or len(rows) != len(plan):
        raise ValueError(
            "calendar check requires the complete rolepack report registration"
        )
    actions = []
    for expected, row in zip(plan, rows, strict=True):
        if not isinstance(row, dict) or any(
            type(row.get(key)) is not type(value) or row.get(key) != value
            for key, value in expected.items()
        ):
            raise ValueError("calendar check report row does not match the plan")
        if expected["task"] != "calendar":
            continue
        state, outcome = row.get("state"), row.get("outcome")
        if state in ("unavailable", "unclaimed", "incomplete") and outcome is None:
            continue
        if state != "complete" or expected["prompt_sha256"] is None:
            raise ValueError("invalid calendar check report state")
        if not isinstance(outcome, dict) or outcome.keys() != {"values", "correct"}:
            raise ValueError("invalid calendar check action")
        outcome = cast(_Document, outcome)
        values, correct = outcome["values"], outcome["correct"]
        if (
            not isinstance(values, list)
            or len(values) != 1
            or type(values[0]) is not int
            or not 0 <= values[0] <= 255
            or not isinstance(correct, list)
            or len(correct) != 1
            or type(correct[0]) is not bool
        ):
            raise ValueError("invalid calendar check action or score")
        turn = row.get("turn")
        if not isinstance(turn, dict) or turn.keys() != {
            "prompt_sha256",
            "response_sha256",
        }:
            raise ValueError("invalid calendar check turn receipt")
        turn = cast(_Document, turn)
        digest = turn["response_sha256"]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(digit not in "0123456789abcdef" for digit in digest)
            or turn["prompt_sha256"] != expected["prompt_sha256"]
        ):
            raise ValueError(
                "calendar check response does not match the registered prompt"
            )
        actions.append((expected["record_id"], values[0], correct[0]))
    return actions


def check(root: Path, pack: _Document, report: _Document) -> _Document:
    from . import protocol

    validated = protocol.validate(pack)
    if report.get("schema") != "seatlab.rolepack-report/v1" or report.get(
        "pack_sha256"
    ) != protocol.digest(validated):
        raise ValueError("calendar check report does not match the pack")
    actions = _completed(protocol.plan(validated), report.get("rows"))
    if len(actions) > _MAX_ACTIONS:
        raise ValueError("too many calendar actions")
    result = _worker(root, [(record_id, slot) for record_id, slot, _ in actions])
    records = sorted(
        (record for record in validated["records"] if record["task"] == "calendar"),
        key=lambda record: record["id"],
    )
    if records != result["records"]:
        raise ValueError("calendar pack does not match the published source")
    scores = result["scores"]
    if scores != [score for _, _, score in actions]:
        raise ValueError("calendar score does not match the original scorer")
    return {
        "actions_checked": len(actions),
        "optimal_actions": sum(scores),
        "sources_checked": len({record_id for record_id, _, _ in actions}),
    }


def _main() -> int:
    try:
        raw = sys.stdin.buffer.read(_REQUEST_LIMIT + 1)
        if len(sys.argv) != 2 or len(raw) > _REQUEST_LIMIT:
            return 1
        actions = json.loads(raw)
        if not isinstance(actions, list) or len(actions) > _MAX_ACTIONS:
            return 1
        result = _stock(_physical(Path(sys.argv[1])), actions)
        sys.stdout.write(
            json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
    except (ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
