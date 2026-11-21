import json
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

from .capsule import _reject_constant, _unique_object, encode_view
from .evidence import _score, read_trial_with_world
from .export_capsules import reconstructed_capsules

_BAYS = frozenset("ABCD")
_INSTRUCTIONS = (
    "Choose one dock bay for each shipment in cases.\n"
    "Your goal is to choose the available bay with the highest utility at "
    "revision 2.\n"
    "If pooled_available is not null, it lists the available bays at revision 2.\n"
    "The newest report in messages has the highest revision number, regardless "
    "of its position. If there is no availability information, use the utilities "
    "alone.\n"
    'Reply with only a JSON object of the form {"rows":[{"id":0,"bay":"A"}]}.\n'
    "Include each shipment ID exactly once. Choose A, B, C, or D for each shipment.\n"
    "Authorized dispatch /view JSON:\n"
)


def _choices(text: str, shipment_ids: set[int]) -> dict[int, str]:
    if not isinstance(text, str):
        raise TypeError("expected JSON reply text")
    try:
        reply = json.loads(
            text, object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
    except RecursionError:
        raise ValueError("JSON reply is too deeply nested") from None
    if not isinstance(reply, dict) or reply.keys() != {"rows"}:
        raise ValueError("expected a JSON object with only rows")
    rows = reply["rows"]
    if not isinstance(rows, list) or len(rows) != len(shipment_ids):
        raise ValueError("expected one row per shipment")
    choices: dict[int, str] = {}
    for row in rows:
        if not isinstance(row, dict) or row.keys() != {"id", "bay"}:
            raise ValueError("expected a row object with only id and bay")
        shipment_id, bay = row["id"], row["bay"]
        if type(shipment_id) is not int or shipment_id not in shipment_ids:
            raise ValueError("expected an integer shipment ID from the view")
        if shipment_id in choices:
            raise ValueError("duplicate shipment ID")
        if not isinstance(bay, str) or bay not in _BAYS:
            raise ValueError("expected bay A, B, C, or D")
        choices[shipment_id] = bay
    return choices


@dataclass(frozen=True)
class Source:
    prompt: str
    metadata: dict[str, Any]
    _dossier: dict[str, Any] = field(repr=False, compare=False)

    def score(self, text: str) -> dict[str, int]:
        cases = self._dossier["cases"]
        choices = _choices(text, {case["id"] for case in cases})
        outcome = _score(
            {
                **self._dossier,
                "cases": [{**case, "chosen": choices[case["id"]]} for case in cases],
            }
        )
        return {key: outcome[key] for key in ("optimal", "feasible", "episodes")}


def load(trial: Path) -> Source:
    receipt, dossier = read_trial_with_world(trial)
    dispatch = reconstructed_capsules(dossier)["dispatch"]
    prompt = _INSTRUCTIONS + encode_view(dispatch["view"]).decode("ascii")
    original = receipt["score"]
    metadata = {
        "native_trial_id_sha256": sha256(
            receipt["trial_id"].encode("ascii")
        ).hexdigest(),
        "basis": dispatch["basis"],
        "view_sha256": dispatch["sha256"],
        "prompt_sha256": sha256(prompt.encode("utf-8")).hexdigest(),
        "condition": receipt["condition"],
        "shipment_count": original["episodes"],
        "original_native_reward": original["optimal"] / original["episodes"],
    }
    return Source(prompt=prompt, metadata=metadata, _dossier=dossier)
