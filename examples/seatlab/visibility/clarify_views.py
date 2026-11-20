"""Build role-scoped dispatcher prompts and score decisions on a verified packet."""

from typing import Any

from ..capsule import encode_view
from ..evidence import _score
from .clarify_schedule import Cell
from .dispatch_packet import _load_json, _replay

_BAYS = ("A", "B", "C", "D")
_SELECT = (
    "You are the dispatcher. You will choose a dock bay for each case in the next "
    "turn. For each case, prefer the available bay with the highest utility. Use "
    "the bulletin with the highest revision number, not the last bulletin to arrive. "
    "Request one revision 2 bulletin that would help with your dispatch. You may "
    "request any case ID, even if you already have its revision 2 bulletin. "
    'Return only a JSON object of the form {"case":0}. Do not include explanations.'
)
_DISPATCH = (
    "You are the dispatcher. The immediate delivery is the last bulletin in messages "
    "for delivered_case. All previous bulletins are still present. If that revision "
    "was already present, its duplicate is included. Choose the available dock bay "
    "with the highest utility for each case. Use pooled_available if present. Otherwise "
    "use the bulletin with the highest revision number you received; arrival order "
    "can differ from revision order. If no availability information is visible, choose "
    "the bay with the highest utility. "
    'Return {"rows":[{"id":0,"bay":"A"}]} with one row per input ID. '
    "Output only a JSON object. Do not include explanations."
)


def _world_cases(packet: dict[str, Any], cell: Cell) -> list[dict[str, Any]]:
    if packet["seed"] != cell.seed:
        raise ValueError("packet does not match the cell's source world")
    return packet["world"]


def _view(
    packet: dict[str, Any], cell: Cell, delivered_case: int | None = None
) -> dict[str, Any]:
    world = _world_cases(packet, cell)
    inventory = {
        row["id"]: {
            report["revision"]: {
                "revision": report["revision"],
                "available": report["available"],
            }
            for report in row["reports"]
        }
        for row in packet["arms"]["fresh"]["inventory"]["rows"]
    }
    hidden = cell.hidden
    cases = []
    for case in world:
        case_id = case["id"]
        reports = inventory[case_id]
        messages = [reports[1]]
        if case_id not in hidden:
            messages.append(reports[2])
        if case_id == delivered_case:
            # Preserve a redundant delivery as a second copy in arrival order.
            messages.append(reports[2])
        cases.append(
            {
                "id": case_id,
                "utilities": case["utilities"],
                "messages": messages,
                "pooled_available": None,
            }
        )
    view: dict[str, Any] = {"role": "dispatch", "cases": cases}
    if delivered_case is not None:
        view["delivered_case"] = delivered_case
    return view


def build_initial(packet: dict[str, Any], cell: Cell) -> str:
    """Show the same initial reports in both arms and ask for one case ID."""
    return f"{_SELECT}\n{encode_view(_view(packet, cell)).decode('ascii')}"


def build_final(packet: dict[str, Any], cell: Cell, selected_case: int) -> str:
    """Show the actual delivery, never the unserved request or the delivery policy."""
    view = _view(packet, cell, cell.delivery(selected_case))
    return f"{_DISPATCH}\n{encode_view(view).decode('ascii')}"


def _response(text: str) -> Any:
    try:
        return _load_json(text)
    except (TypeError, ValueError, RecursionError):
        raise ValueError("expected valid JSON without duplicate keys") from None


def validate_selector(text: str) -> int:
    """Accept exactly one integer case ID, including an already visible case."""
    selection = _response(text)
    if not isinstance(selection, dict) or selection.keys() != {"case"}:
        raise ValueError("expected exactly one case field")
    case_id = selection["case"]
    if type(case_id) is not int or case_id not in range(4):
        raise ValueError("expected an integer case ID from 0 to 3")
    return case_id


def validate_actions(packet: dict[str, Any], cell: Cell, text: str) -> dict[str, Any]:
    """Accept one valid bay choice per case and normalize the row order."""
    expected = {case["id"] for case in _world_cases(packet, cell)}
    actions = _response(text)
    if not isinstance(actions, dict) or actions.keys() != {"rows"}:
        raise ValueError("expected exactly one rows field")
    rows = actions["rows"]
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ValueError("expected one dispatch row per case")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or row.keys() != {"id", "bay"}:
            raise ValueError("expected exactly the id and bay fields in each row")
        case_id, bay = row["id"], row["bay"]
        if type(case_id) is not int or case_id not in expected or case_id in seen:
            raise ValueError("expected distinct case IDs from the input roster")
        if not isinstance(bay, str) or bay not in _BAYS:
            raise ValueError("expected a bay A, B, C, or D")
        seen.add(case_id)
    return {"rows": sorted(rows, key=lambda row: row["id"])}


def grade_actions(
    packet: dict[str, Any], cell: Cell, actions: dict[str, Any]
) -> dict[str, Any]:
    """Use the stock scorer on the original world, not the delivered reports."""
    _world_cases(packet, cell)
    # The stock dossier requires a native delivery label. This replay is only for
    # scoring choices against true availability, not for reconstructing our prompts.
    world = _replay(cell.seed, "fresh", packet["arms"]["fresh"]["inventory"], actions)
    score = _score(world)
    return {
        "episodes": score["episodes"],
        "optimal": score["optimal"],
        "feasible": score["feasible"],
        "by_case": [
            {key: row[key] for key in ("id", "optimal", "feasible")}
            for row in score["rows"]
        ],
    }
