import sys
from hashlib import sha256
from pathlib import Path

from .. import calendar_case
from ..receiver_panel import _MAX_LINE_BYTES, _MAX_REPLY_BYTES, _worker_line
from ..visibility import calendar_packet
from . import clarify

type _Envelope = tuple[str, str, str]


def _read(path: Path, limit: int) -> bytes:
    with calendar_case._directory(path.parent) as directory:
        return calendar_case._read_at(directory, path.name, limit)


def _phase(path: Path | None) -> _Envelope | None:
    if path is None:
        return None
    data = _read(path, _MAX_LINE_BYTES)
    if not data:
        return None
    if data.count(b"\n") != 1:
        raise ValueError("expected exactly one worker line")
    return _worker_line(data)


def _bind(envelope: _Envelope, trial_uuid: str, phase: str, prompt: str) -> bytes:
    case_id, commitment, reply = envelope
    request = calendar_case._request(f"{trial_uuid}/{phase}", prompt)
    if case_id != request["id"] or commitment != request["prompt_sha256"]:
        raise ValueError("unexpected worker ID or commitment")
    encoded = reply.encode("utf-8")
    if len(encoded) > _MAX_REPLY_BYTES:
        raise ValueError("reply exceeds the byte limit")
    return encoded


def _unreachable(*envelopes: _Envelope | None) -> None:
    if any(envelope is not None for envelope in envelopes):
        raise ValueError("unexpected worker reply for an unreachable phase")


def _decision(
    target: int, envelope: _Envelope | None, trial_uuid: str, prompt: str
) -> dict[str, object]:
    observed = None
    if envelope is not None:
        encoded = _bind(envelope, trial_uuid, "receiver-booking", prompt)
        observed = (envelope[2], sha256(encoded).hexdigest())
    score = calendar_case._score(target, observed)
    return {"status": score["status"], "correct": score["correct"]}


def _row(
    capsule: calendar_case._Capsule,
    sender: tuple[str, calendar_packet.Table],
    *,
    nomination: _Envelope | None,
    confirmation: _Envelope | None,
    blind: _Envelope | None,
    peer: _Envelope | None,
) -> dict[str, object]:
    trial_uuid, table = sender
    receiver = clarify._receiver(calendar_case._wire(capsule.requests["checked"]))
    if trial_uuid != capsule.source["trial_uuid"] or trial_uuid != receiver.trial_uuid:
        raise ValueError("sender and receiver trial UUIDs differ")
    sender_starts = set(calendar_packet.starts(table))
    target = capsule.target
    if sender_starts.intersection(receiver.own) != {target}:
        raise ValueError("expected exactly the owner target to be jointly feasible")
    row: dict[str, object] = {
        **{
            key: capsule.source[key]
            for key in ("world", "arm", "trial_uuid", "result_sha256")
        },
        "nomination_status": "missing",
        "target_nominated": None,
        "sender_status": "not_attempted",
        "sender_exact": None,
        "target_confirmed": None,
        "blind": {"status": "not_attempted", "correct": None},
        "peer": {"status": "not_attempted", "correct": None},
    }
    if nomination is None:
        _unreachable(confirmation, blind, peer)
        return row
    _bind(nomination, trial_uuid, "receiver-nomination", clarify._nomination(receiver))
    try:
        proposed = clarify._local_proposals(
            receiver, clarify._nomination_reply(nomination[2])
        )
    except (ValueError, RecursionError):
        _unreachable(confirmation, blind, peer)
        row["nomination_status"] = "invalid"
        return row
    row.update(nomination_status="accepted", target_nominated=target in proposed)
    if confirmation is None:
        _unreachable(blind, peer)
        row["sender_status"] = "missing"
        return row
    _bind(
        confirmation,
        trial_uuid,
        "sender-verification",
        clarify.verification(table, proposed),
    )
    try:
        confirmed = clarify.confirmation(proposed, confirmation[2])
    except (ValueError, RecursionError):
        _unreachable(blind, peer)
        row["sender_status"] = "invalid"
        return row
    row.update(
        sender_status="accepted",
        sender_exact=confirmed
        == tuple(slot for slot in proposed if slot in sender_starts),
        target_confirmed=target in confirmed,
        blind=_decision(
            target, blind, trial_uuid, clarify._booking(receiver, proposed, None)
        ),
        peer=_decision(
            target, peer, trial_uuid, clarify._booking(receiver, proposed, confirmed)
        ),
    )
    return row


def main(argv: list[str] | None = None) -> int:
    parser = calendar_case._Parser(
        allow_abbrev=False,
        description=(
            "Score first clarification replies for one trusted native export. "
            "This does not authenticate the private files or call a model."
        ),
    )
    parser.add_argument("case", type=Path, help="Native owner capsule directory.")
    parser.add_argument(
        "--sender", type=Path, required=True, help="Native sender table export."
    )
    for name in ("nomination", "confirmation", "blind", "peer"):
        parser.add_argument(
            f"--{name}",
            type=Path,
            help="One worker envelope with LF. Omit or use an empty file if absent.",
        )
    args = parser.parse_args(argv)
    try:
        row = _row(
            calendar_case._load_capsule(args.case),
            clarify._sender(_read(args.sender, clarify._MAX_SENDER_BYTES)),
            nomination=_phase(args.nomination),
            confirmation=_phase(args.confirmation),
            blind=_phase(args.blind),
            peer=_phase(args.peer),
        )
        sys.stdout.buffer.write(calendar_case._wire(row))
    except (OSError, RecursionError, TypeError, ValueError):
        print("Cannot score calendar clarification case.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
