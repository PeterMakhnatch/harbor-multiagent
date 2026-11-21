import sys
from pathlib import Path

from .. import calendar_case
from ..visibility import calendar_packet
from . import clarify, clarify_case, second_question


def _inputs(
    capsule: calendar_case._Capsule, sender: tuple[str, calendar_packet.Table]
) -> tuple[bytes, clarify._Receiver]:
    trial_uuid, table = sender
    checked = calendar_case._wire(capsule.requests["checked"])
    receiver = clarify._receiver(checked)
    if trial_uuid != capsule.source["trial_uuid"] or trial_uuid != receiver.trial_uuid:
        raise ValueError("sender and receiver trial UUIDs differ")
    clarify._check_sender_table(table)
    sender_starts = set(calendar_packet.starts(table))
    if sender_starts.intersection(receiver.own) != {capsule.target}:
        raise ValueError("expected exactly the owner target to be jointly feasible")
    return checked, receiver


def _public_row(source: dict[str, str]) -> dict[str, object]:
    return {
        "world": source["world"],
        "arm": source["arm"],
        "trial_uuid": source["trial_uuid"],
        "result_sha256": source["result_sha256"],
        "prior_status": "missing",
        "prior_target": None,
        "remaining": None,
        "opportunity": 0,
        "extra_status": "not_attempted",
        "extra_target": None,
        "stop": {"status": "not_attempted", "correct": None},
        "long": {"status": "not_attempted", "correct": None},
    }


def _row(
    capsule: calendar_case._Capsule,
    sender: tuple[str, calendar_packet.Table],
    *,
    prior: clarify_case._Envelope | None,
    extra: clarify_case._Envelope | None,
    stop: clarify_case._Envelope | None,
    long: clarify_case._Envelope | None,
) -> dict[str, object]:
    checked, receiver = _inputs(capsule, sender)
    trial_uuid, table = sender
    target = capsule.target
    row = _public_row(capsule.source)
    if prior is None:
        clarify_case._unreachable(extra, stop, long)
        return row
    clarify_case._bind(
        prior, trial_uuid, "receiver-nomination", clarify._nomination(receiver)
    )
    try:
        proposed = clarify._local_proposals(
            receiver, clarify._nomination_reply(prior[2])
        )
    except (ValueError, RecursionError):
        clarify_case._unreachable(extra, stop, long)
        row["prior_status"] = "invalid"
        return row

    verified = clarify.verified_subset(table, proposed)
    count = second_question.opportunity(checked, proposed, verified)
    stop_prompt = second_question.choice(checked, proposed, verified)
    stop_score = clarify_case._decision(target, stop, trial_uuid, stop_prompt)
    row.update(
        prior_status="accepted",
        prior_target=target in proposed,
        remaining=len(receiver.own) - len(proposed),
        opportunity=count,
        stop=stop_score,
    )
    if count == 0:
        clarify_case._unreachable(extra, long)
        row["long"] = stop_score
        return row
    if extra is None:
        clarify_case._unreachable(long)
        row["extra_status"] = "missing"
        return row

    question = second_question.question(checked, proposed, verified)
    clarify_case._bind(extra, trial_uuid, "receiver-second-question", question)
    try:
        additional = second_question.additional(checked, proposed, verified, extra[2])
    except (ValueError, RecursionError):
        clarify_case._unreachable(long)
        row["extra_status"] = "invalid"
        return row
    confirmed = second_question.verify(table, additional)
    long_prompt = second_question.choice(
        checked, proposed, verified, additional, confirmed
    )
    row.update(
        extra_status="accepted",
        extra_target=target in additional,
        long=clarify_case._decision(target, long, trial_uuid, long_prompt),
    )
    return row


def main(argv: list[str] | None = None) -> int:
    parser = calendar_case._Parser(
        allow_abbrev=False, description="Score a native calendar second question case."
    )
    parser.add_argument("case", type=Path, help="Native owner capsule directory.")
    parser.add_argument("--sender", type=Path, required=True)
    parser.add_argument("--prior", type=Path, required=True)
    parser.add_argument("--extra", type=Path)
    parser.add_argument("--stop", type=Path)
    parser.add_argument("--long", type=Path)
    args = parser.parse_args(argv)
    try:
        row = _row(
            calendar_case._load_capsule(args.case),
            clarify._sender(clarify_case._read(args.sender, clarify._MAX_SENDER_BYTES)),
            prior=clarify_case._phase(args.prior),
            extra=clarify_case._phase(args.extra),
            stop=clarify_case._phase(args.stop),
            long=clarify_case._phase(args.long),
        )
        if (
            (row["prior_status"] != "accepted" and args.stop is not None)
            or (row["opportunity"] == 0 and args.extra is not None)
            or (row["extra_status"] != "accepted" and args.long is not None)
        ):
            raise ValueError("unexpected file for an unreachable phase")
        sys.stdout.buffer.write(calendar_case._wire(row))
    except (OSError, RecursionError, TypeError, ValueError):
        print("Cannot score calendar second question case.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
