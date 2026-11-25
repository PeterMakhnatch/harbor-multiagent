import argparse
import hashlib
import json
import os
import shutil
import stat
import tempfile
from importlib import import_module
from pathlib import Path
from typing import Any

from ..native_study import record_bytes
from ..seat_checkpoints.materialize import sync_directory
from ..ship_inflight.materialize import publish
from .exhibit_authored import INSTRUCTION, MODES, WORLD, canonical, packet, withheld
from .exhibit_enroll import target, write_private
from .exhibit_pairs import (
    FILE_FLAGS,
    reject_constant,
    reject_float,
    require,
    unique_object,
)
from .exhibit_wire import request_stamp

SCHEMA = "seatlab.exhibit-saved/v1"
MAX_CUT_BYTES = 128 * 1024
MAX_PACKET_BYTES = 128 * 1024
FILES = {"authored": "shown.request.jsonl", "author-masked": "masked.request.jsonl"}


def accepted_prefix(cut: dict[str, Any]) -> dict[str, Any]:
    require(
        type(cut) is dict and cut.keys() == {"version", "case", "mode", "events"},
        "invalid cut fields",
    )
    require(cut["mode"] in MODES, "expected registrar-authored mode")
    case, events = cut["case"], cut["events"]
    require(
        type(case) is dict and case.get("profile") == "coupling",
        "expected coupling case",
    )
    require(
        type(events) is list and 2 <= len(events) <= 4,
        "expected two to four accepted events",
    )
    prefix = {**cut, "events": events[:2]}
    grade = import_module(f"{WORLD}.grading").grade
    grade(cut)
    return prefix


def owner_view(prefix: dict[str, Any], mode: str) -> dict[str, Any]:
    session = import_module(f"{WORLD}.engine").Session(prefix["case"], mode)
    for event in prefix["events"]:
        session.apply(event["role"], event["action"])
    require(
        canonical(session.snapshot()) == canonical({**prefix, "mode": mode}),
        "accepted prefix changed during replay",
    )
    view = session.view("depot")
    require(
        view["role"] == view["phase"] == view["turn"] == "depot"
        and view["bundle_source"] == "registrar"
        and view["order"] is None
        and view["briefs"] == view["registrar_file"] == view["depot_file"] == [],
        "invalid depot role view",
    )
    return view


def packets(
    cut: dict[str, Any], instruction: str
) -> tuple[dict[str, bytes], dict[str, Any]]:
    require(
        type(instruction) is str and bool(instruction.strip()),
        "missing depot instruction",
    )
    prefix = accepted_prefix(cut)
    shown, masked = (owner_view(prefix, mode) for mode in MODES)
    notes = prefix["events"][1]["action"]["notes"]
    require(shown["bundles"] == notes, "accepted notes changed")
    expected = {**shown, "bundles": withheld(notes)}
    require(
        canonical(masked) == canonical(expected),
        "masked role view changed beyond weights",
    )
    left, right = packet(instruction, shown), packet(instruction, masked)
    require(
        right == packet(instruction, expected), "masked packet changed beyond weights"
    )
    require(
        len(left) == len(right) <= MAX_PACKET_BYTES
        and left.isascii()
        and right.isascii(),
        "invalid packet size or encoding",
    )
    changes = [(a, b) for a, b in zip(left, right, strict=True) if a != b]
    count = sum(row["weight"] != 0 for row in notes)
    require(
        len(changes) == count and all(49 <= a <= 57 and b == 48 for a, b in changes),
        "unexpected packet changes",
    )
    requests = dict(zip(MODES, (left, right), strict=True))
    case, events = prefix["case"], prefix["events"]
    audit = import_module(f"{WORLD}.notes").audit_notes(
        case, events[0]["action"]["slips"], events[1]["action"]["ids"], notes
    )
    receipt = {
        "schema": SCHEMA,
        "evidence": "declared_local_prefix",
        "provider_authenticated": False,
        "executions": 0,
        "source_mode": prefix["mode"],
        "profile": "coupling",
        "accepted_event_count": 2,
        "case_sha256": hashlib.sha256(canonical(case)).hexdigest(),
        "accepted_prefix_sha256": hashlib.sha256(
            canonical({"case": case, "events": events})
        ).hexdigest(),
        "packets": {
            mode: {"file": FILES[mode], **request_stamp(data)}
            for mode, data in requests.items()
        },
        "weight_only_byte_changes": count,
        "submitted_source_audit": audit,
    }
    return requests, receipt


def read_cut(path: Path) -> dict[str, Any]:
    with os.fdopen(os.open(path, FILE_FLAGS), "rb") as handle:
        before = os.fstat(handle.fileno())
        require(
            stat.S_ISREG(before.st_mode)
            and stat.S_IMODE(before.st_mode) == 0o600
            and before.st_uid == os.geteuid()
            and 0 < before.st_size <= MAX_CUT_BYTES,
            "expected a private bounded cut file",
        )
        data = handle.read(MAX_CUT_BYTES + 1)
        after = os.fstat(handle.fileno())
        require(
            len(data) == before.st_size == after.st_size <= MAX_CUT_BYTES
            and before.st_mtime_ns == after.st_mtime_ns
            and before.st_ctime_ns == after.st_ctime_ns,
            "cut changed during read",
        )
    cut = json.loads(
        data.decode("utf-8"),
        object_pairs_hook=unique_object,
        parse_constant=reject_constant,
        parse_float=reject_float,
    )
    require(type(cut) is dict, "expected cut object")
    return cut


def freeze(cut_path: Path, out: Path) -> dict[str, Any]:
    out = target(out)
    requests, receipt = packets(
        read_cut(cut_path), INSTRUCTION.read_text(encoding="utf-8")
    )
    stage = Path(tempfile.mkdtemp(prefix=".exhibit-saved-", dir=out.parent))
    try:
        os.chmod(stage, 0o700)
        for mode in MODES:
            write_private(stage / FILES[mode], requests[mode])
        write_private(stage / "receipt.json", record_bytes(receipt))
        sync_directory(stage)
        publish(stage, out)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    sync_directory(out.parent)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        allow_abbrev=False,
        description="Freeze a declared local authored prefix into two private depot requests. Never launches.",
    )
    parser.add_argument("cut", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    try:
        freeze(args.cut, args.output)
    except (
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        AttributeError,
        OSError,
        OverflowError,
        RecursionError,
    ):
        parser.exit(2, "exhibit saved: invalid private input or publication failed\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
