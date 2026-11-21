import re
import sys
from uuid import UUID

from .. import calendar_case
from ..visibility import calendar_packet
from . import protocol as p
from . import team_views

_SCHEMA = "seatlab.calendar-booking-route/v1"
_CAPABILITIES = {
    "decision_schema": _SCHEMA,
    "input": "calendar-case/checked",
    "policy": "unique-visible-candidate",
    "router_model_calls": 0,
}
_CHECKED_PROMPT = re.compile(
    re.escape(team_views._VIEW_PREFIX + team_views._OWN_STARTS)
    + r"(?P<own>[^\n]+)"
    + re.escape(".\n\nTask:\n" + p._DAY_IDS + team_views._INITIAL_REPORT)
    + r"(?P<initial>[^\n]+)\n"
    + re.escape(team_views._OPENED_REPORTS)
    + r"(?P<reports>[^\n]+)\n"
    + re.escape(team_views._CHOICE_RULES + "\n" + team_views._VISIBLE)
    + r"(?P<visible>[^\n]+)\n"
    + re.escape(team_views._CHECK_LIMITS)
)


def _checked_request(data: bytes) -> calendar_case._Request:
    if not 0 < len(data) <= calendar_case._MAX_LINE_BYTES:
        raise ValueError("expected one bounded request")
    record = calendar_packet._object(
        calendar_case._json(data), "id", "prompt_sha256", "messages"
    )
    case_id = record["id"]
    if not isinstance(case_id, str):
        raise TypeError("expected a checked case ID")
    trial_uuid, _, view = case_id.partition("/")
    if (
        view != "checked"
        or len(trial_uuid) != 36
        or str(UUID(trial_uuid)) != trial_uuid
    ):
        raise ValueError("expected a canonical checked case ID")
    return calendar_case._parse_request(
        data, case_id, calendar_packet._check_digest(record["prompt_sha256"])
    )


def _prompt_field(text: str) -> object:
    value = calendar_packet._json(text)
    if p.wire(value) != text:
        raise ValueError("noncanonical prompt field")
    return value


def _starts(value: object) -> tuple[int, ...]:
    return p._starts(calendar_packet._list(value))


def _candidates(prompt: str) -> tuple[int, ...]:
    match = _CHECKED_PROMPT.fullmatch(prompt)
    if match is None:
        raise ValueError("expected a checked choice prompt")
    own = set(_starts(_prompt_field(match["own"])))
    initial = _starts(_prompt_field(match["initial"]))
    reports = team_views._opened_starts(
        calendar_packet._object(_prompt_field(match["reports"]), "reports")
    )
    visible = calendar_packet._object(
        _prompt_field(match["visible"]), "opened", "unopened_initial"
    )
    opened = _starts(visible["opened"])
    unopened = _starts(visible["unopened_initial"])
    if not set(opened).isdisjoint(unopened):
        raise ValueError("overlapping visible starts")
    report_slots = (slot for day in reports.values() for slot in day)
    if opened != tuple(sorted(own.intersection(report_slots))):
        raise ValueError("opened starts do not match the displayed reports")
    if unopened != tuple(s for s in initial if s in own and p._day(s) not in reports):
        raise ValueError("unopened starts do not match the displayed reports")
    return opened + unopened


def _route(data: bytes) -> dict[str, object]:
    request = _checked_request(data)
    candidates = _candidates(request["messages"][1]["content"])
    decision: dict[str, object] = {
        "schema": _SCHEMA,
        "id": request["id"],
        "prompt_sha256": request["prompt_sha256"],
    }
    if len(candidates) == 1:
        return {
            **decision,
            "action": "book",
            "reason": "single_visible_candidate",
            "booking": {"slot": candidates[0]},
        }
    return {
        **decision,
        "action": "defer",
        "reason": "multiple_visible_candidates"
        if candidates
        else "no_visible_candidates",
        "request": request,
    }


def main() -> int:
    parser = calendar_case._Parser(
        allow_abbrev=False,
        description="Read one checked calendar request from stdin and emit a role-only JSON decision.",
    )
    parser.add_argument(
        "--capabilities", action="store_true", help="Print the router capability."
    )
    args = parser.parse_args()
    try:
        result = (
            _CAPABILITIES
            if args.capabilities
            else _route(sys.stdin.buffer.read(calendar_case._MAX_LINE_BYTES + 1))
        )
        sys.stdout.buffer.write(calendar_case._wire(result))
    except (OSError, RecursionError, TypeError, ValueError):
        print("Cannot route checked calendar request.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
