import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from .. import calendar_case
from ..visibility import calendar_packet, calendar_protocol
from . import clarify, team_views
from . import protocol as p


@dataclass(frozen=True, slots=True)
class _First:
    receiver: clarify._Receiver
    prior: tuple[int, int]
    verified: tuple[int, ...]

    @property
    def count(self) -> int:
        return 0 if self.verified else min(2, len(self.receiver.own) - 2)


def _first(checked: bytes, prior: tuple[int, int], verified: tuple[int, ...]) -> _First:
    receiver = clarify._receiver(checked)
    prior = clarify._local_proposals(receiver, prior)
    return _First(receiver, prior, clarify._fits(prior, verified))


def opportunity(
    checked: bytes, prior: tuple[int, int], verified: tuple[int, ...]
) -> int:
    return _first(checked, prior, verified).count


def _require_open(first: _First) -> int:
    count = first.count
    if count == 0:
        raise ValueError("no second question opportunity")
    return count


def _evidence(proposed: tuple[int, ...], verified: tuple[int, ...]) -> str:
    return (
        f"Nominated slot IDs: {p.wire(proposed)}\n"
        f"Verification reply: {p.wire({'fits': verified})}\n"
    )


def _question(first: _First) -> str:
    count = _require_open(first)
    prefix, _, tail = first.receiver.prompt.partition(team_views._CHOICE_RULES)
    visible, _, _ = tail.partition(team_views._CHECK_LIMITS)
    reply = '{"proposals":[n]}' if count == 1 else '{"proposals":[n,m]}'
    return (
        prefix
        + clarify._REPORT_RULES.rstrip()
        + visible
        + "\nClarification:\n"
        + _evidence(first.prior, first.verified)
        + "Neither nominated start fits the other calendar. "
        f"Nominate exactly {count} other starts from your own calendar list. "
        "Do not repeat either previous slot ID. "
        f"Return only JSON: {reply}. Use distinct slot IDs in ascending order."
    )


def question(checked: bytes, prior: tuple[int, int], verified: tuple[int, ...]) -> str:
    return _question(_first(checked, prior, verified))


def _one_or_two(proposed: object) -> tuple[int, ...]:
    if not isinstance(proposed, tuple) or not 1 <= len(proposed) <= 2:
        raise ValueError("expected one or two proposed slot IDs")
    return calendar_protocol._checked_starts(proposed)


def _additional(first: _First, proposed: object) -> tuple[int, ...]:
    count = _require_open(first)
    slots = _one_or_two(proposed)
    if len(slots) != count:
        raise ValueError("unexpected number of additional slot IDs")
    if any(slot not in first.receiver.own or slot in first.prior for slot in slots):
        raise ValueError("expected previously untested locally feasible starts")
    return slots


def _raw_proposals(raw: str) -> tuple[object, ...]:
    return tuple(clarify._reply_array(raw, "proposals"))


def additional(
    checked: bytes, prior: tuple[int, int], verified: tuple[int, ...], raw: str
) -> tuple[int, ...]:
    first = _first(checked, prior, verified)
    return _additional(first, _raw_proposals(raw))


def verify(table: calendar_packet.Table, proposed: tuple[int, ...]) -> tuple[int, ...]:
    proposed = _one_or_two(proposed)
    if len(proposed) == 2:
        return clarify.verified_subset(table, (proposed[0], proposed[1]))
    clarify._check_sender_table(table)
    return proposed if proposed[0] in calendar_packet.starts(table) else ()


def _extra_fits(proposed: tuple[int, ...], verified: object) -> tuple[int, ...]:
    if len(proposed) == 2:
        return clarify._fits((proposed[0], proposed[1]), verified)
    if not isinstance(verified, tuple) or len(verified) > 1:
        raise ValueError("expected at most one verified slot ID")
    if not verified:
        return ()
    if calendar_protocol._checked_slot(verified[0]) != proposed[0]:
        raise ValueError("verified start was not nominated")
    return proposed


def _choice(first: _First, extra: object, extra_verified: object) -> str:
    evidence = _evidence(first.prior, first.verified)
    if extra is not None:
        proposed = _additional(first, extra)
        evidence += _evidence(proposed, _extra_fits(proposed, extra_verified))
    elif extra_verified is not None:
        raise ValueError("verification reply has no additional nominations")
    return (
        f"{first.receiver.prompt}\n\nClarification:\n{evidence}"
        "Each verification reply concerns only the nominated starts shown with it. "
        "Select one joint start from your own calendar list. You may choose any "
        f"start in that list, not only a nominated start.{clarify._SLOT_REPLY}"
    )


def choice(
    checked: bytes,
    prior: tuple[int, int],
    verified: tuple[int, ...],
    extra: tuple[int, ...] | None = None,
    extra_verified: tuple[int, ...] | None = None,
) -> str:
    return _choice(_first(checked, prior, verified), extra, extra_verified)


def _read(path: Path, limit: int) -> bytes:
    with calendar_case._directory(path.parent) as directory:
        return calendar_case._read_at(directory, path.name, limit)


def _read_reply(path: Path) -> str:
    return _read(path, clarify._MAX_TRANSFER_BYTES).decode("utf-8")


def _receiver_result(args: argparse.Namespace) -> object:
    checked = _read(args.checked, calendar_case._MAX_LINE_BYTES)
    prior = clarify.nominees(checked, _read_reply(args.prior))
    verified = clarify.confirmation(prior, _read_reply(args.verified))
    first = _First(clarify._receiver(checked), prior, verified)
    if args.phase == "opportunity":
        return {"count": first.count}
    if args.phase == "ask":
        return calendar_case._request(
            f"{first.receiver.trial_uuid}/receiver-second-question", _question(first)
        )
    if args.phase == "admit":
        return {
            "proposals": _additional(first, _raw_proposals(_read_reply(args.extra)))
        }
    extra = None if args.extra is None else _raw_proposals(_read_reply(args.extra))
    extra_verified = (
        None
        if args.extra_verified is None
        else tuple(clarify._reply_array(_read_reply(args.extra_verified), "fits"))
    )
    return calendar_case._request(
        f"{first.receiver.trial_uuid}/receiver-booking",
        _choice(first, extra, extra_verified),
    )


def main(argv: list[str] | None = None) -> int:
    parser = calendar_case._Parser(
        allow_abbrev=False, description="Prepare or verify one bounded second question."
    )
    phases = parser.add_subparsers(dest="phase", required=True)
    for phase in ("opportunity", "ask", "admit", "book"):
        receiver = phases.add_parser(phase, allow_abbrev=False)
        receiver.add_argument("--checked", type=Path, required=True)
        receiver.add_argument("--prior", type=Path, required=True)
        receiver.add_argument("--verified", type=Path, required=True)
        if phase in ("admit", "book"):
            receiver.add_argument("--extra", type=Path, required=phase == "admit")
        if phase == "book":
            receiver.add_argument("--extra-verified", type=Path)
    sender = phases.add_parser("verify-local", allow_abbrev=False)
    sender.add_argument("--sender", type=Path, required=True)
    sender.add_argument("--extra", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.phase == "verify-local":
            _, table = clarify._sender(_read(args.sender, clarify._MAX_SENDER_BYTES))
            proposed = _one_or_two(_raw_proposals(_read_reply(args.extra)))
            result: object = {"fits": verify(table, proposed)}
        else:
            result = _receiver_result(args)
        sys.stdout.buffer.write(calendar_case._wire(result))
    except (OSError, RecursionError, TypeError, ValueError):
        print("Cannot prepare calendar second question request.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
