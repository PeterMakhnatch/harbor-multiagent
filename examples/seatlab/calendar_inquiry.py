import os
import sys
from contextlib import suppress
from pathlib import Path

from .calendar_case import _directory, _json, _jsonl, _Parser, _read_at, _wire
from .calendar_inquiry_protocol import advance, parse_source
from .receiver_panel import _MAX_LINE_BYTES, _worker_line

_MAX_OWNER_BYTES = 32 * 1024 - 1
_MAX_HISTORY_BYTES = 3 * _MAX_LINE_BYTES


def _read_file(path: Path, limit: int) -> bytes:
    with _directory(path.parent) as directory:
        return _read_at(directory, path.name, limit)


def _write_owner(path: Path, data: bytes) -> None:
    if len(data) > _MAX_OWNER_BYTES:
        raise ValueError("owner file exceeds the byte limit")
    with _directory(path.parent) as directory:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        fd = os.open(path.name, flags, 0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(fd, 0o600)
                stream.write(data)
        except BaseException:
            with suppress(OSError):
                os.unlink(path.name, dir_fd=directory)
            raise


def _export(trial: Path, spend_dir: Path, out: Path) -> None:
    from .calendar_case_source import load

    inquiry = load(trial, spend_dir).inquiry
    parse_source(inquiry)
    _write_owner(out, _wire(inquiry))


def _read_history(path: Path) -> list[dict[str, object]]:
    data = _read_file(path, _MAX_HISTORY_BYTES)
    if data and not data.endswith(b"\n"):
        data += b"\n"
    replies: list[dict[str, object]] = []
    for line in _jsonl(data):
        case_id, commitment, reply = _worker_line(line + b"\n")
        replies.append({"id": case_id, "prompt_sha256": commitment, "reply": reply})
    return replies


def main() -> int:
    parser = _Parser(allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", allow_abbrev=False)
    export.add_argument("trial", type=Path)
    export.add_argument("--spend-dir", required=True, type=Path)
    export.add_argument("--out", required=True, type=Path)
    next_step = commands.add_parser(
        "next",
        allow_abbrev=False,
        description=(
            "Emit a role request with messages, or an owner-only terminal record "
            "with status. Send only role requests to workers."
        ),
    )
    next_step.add_argument("owner", type=Path)
    next_step.add_argument("--timing", required=True, choices=("immediate", "delayed"))
    next_step.add_argument("--history", type=Path)
    try:
        args = parser.parse_args()
        if args.command == "export":
            _export(args.trial, args.spend_dir, args.out)
        else:
            source = parse_source(_json(_read_file(args.owner, _MAX_OWNER_BYTES)))
            replies = [] if args.history is None else _read_history(args.history)
            sys.stdout.buffer.write(_wire(advance(source, args.timing, replies)))
    except (ImportError, KeyError, OSError, RecursionError, TypeError, ValueError):
        print("Cannot process calendar inquiry.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
