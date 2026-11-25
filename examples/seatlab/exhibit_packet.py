import argparse
import hashlib
import json
from importlib import import_module
from pathlib import Path
from typing import Any

from .exhibit_wire import encode_request, render_turn

_TASK = "examples.tasks.exhibit-relay"
make_case = import_module(f"{_TASK}.environment.exhibit_relay.cases").make_case
Session = import_module(f"{_TASK}.environment.exhibit_relay.engine").Session
act = import_module(f"{_TASK}.solution.policies").act
_VIEW_FIELDS = {
    "role",
    "phase",
    "turn",
    "quota",
    "nominate",
    "exhibits",
    "crates",
    "bays",
    "briefs",
    "cards",
    "agenda",
    "bundles",
    "registrar_file",
    "depot_file",
    "order",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _view(session: Session, role: str) -> dict[str, Any]:
    return json.loads(
        json.dumps(session.view(role), sort_keys=True, separators=(",", ":"))
    )


def _truth(
    case: dict[str, Any],
) -> tuple[set[str], list[dict[str, Any]], list[dict[str, Any]]]:
    filed = set()
    marginal: dict[tuple[str, int], int] = {}
    bundles = []
    for brief in case["briefs"]:
        epoch = brief["epoch"]
        keys = []
        for item in brief["items"]:
            matches = [
                card
                for card in case["registrar"]
                if card["key"] == item and card["start"] <= epoch <= card["end"]
            ]
            _require(len(matches) == 1, "invalid registrar mapping")
            card = matches[0]
            filed.add(card["id"])
            key = card["value"]
            keys.append(key)
            marginal[key, epoch] = marginal.get((key, epoch), 0) + brief["weight"]
        _require(len(set(keys)) == 2, "invalid pair")
        bundles.append(
            {"epoch": epoch, "keys": sorted(keys), "weight": brief["weight"]}
        )
    _require(len(filed) == len(marginal) == len(bundles) == 4, "incomplete pair truth")
    _require(set(marginal.values()) == {5}, "incorrect marginal truth")
    _require(
        sorted(row["weight"] for row in bundles) == [1, 1, 4, 4],
        "incorrect weight truth",
    )
    agenda = [
        {"key": key, "epoch": epoch, "weight": value}
        for (key, epoch), value in sorted(marginal.items())
    ]
    return filed, agenda, sorted(bundles, key=lambda row: (row["epoch"], row["keys"]))


def _prefix(
    case: dict[str, Any], mode: str
) -> tuple[
    dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]
]:
    session = Session(case, mode)
    loader = _view(session, "loader")
    request = act(loader, "portfolio")
    _require(
        request
        == {"op": "request", "slips": sorted(row["id"] for row in case["briefs"])},
        "incomplete nominations",
    )
    session.apply("loader", request)
    registrar = _view(session, "registrar")
    filing = act(registrar, "portfolio")
    filed, agenda, bundles = _truth(case)
    _require(
        filing == {"op": "file", "ids": sorted(filed)}, "incomplete registrar filing"
    )
    session.apply("registrar", filing)
    depot = _view(session, "depot")
    expected = {
        "role": "depot",
        "phase": "depot",
        "turn": "depot",
        "quota": 2,
        "nominate": 4,
        "exhibits": case["exhibits"],
        "crates": case["crates"],
        "bays": case["bays"],
        "briefs": [],
        "cards": case["depot"],
        "agenda": agenda,
        "bundles": bundles
        if mode == "bundled"
        else [{**row, "weight": 0} for row in bundles],
        "registrar_file": [],
        "depot_file": [],
        "order": None,
    }
    _require(
        depot.keys() == _VIEW_FIELDS and depot == expected,
        "depot scope or pair truth differs",
    )
    return loader, request, registrar, filing, depot


def _envelope(instruction: str, view: dict[str, Any]) -> bytes:
    prompt = render_turn(instruction, view, None)
    _, marker, payload = prompt.partition("\nInput JSON: ")
    _require(bool(marker), "missing owner input payload")
    _require(
        json.loads(payload)
        == {"instruction": instruction, "view": view, "previous_action": None},
        "owner payload differs from role-local input",
    )
    envelope = encode_request(prompt)
    _require(
        type(envelope) is bytes and envelope.endswith(b"\n"),
        "invalid owner envelope framing",
    )
    _require(
        json.loads(envelope) == {"schema": "seatlab.seat-turn/v1", "prompt": prompt},
        "unexpected owner envelope fields",
    )
    return envelope


def _bundle_fragment(view: dict[str, Any]) -> bytes:
    fragment = '"bundles":' + _json(view["bundles"])
    return json.dumps(fragment, ensure_ascii=True)[1:-1].encode("ascii")


def _delta(
    before: bytes, after: bytes, masked: dict[str, Any], bundled: dict[str, Any]
) -> list[int]:
    hidden, visible = _bundle_fragment(masked), _bundle_fragment(bundled)
    _require(
        len(hidden) == len(visible) and len(before) == len(after), "byte lengths differ"
    )
    _require(
        before.count(hidden) == 1 and after.count(visible) == 1,
        "ambiguous bundle field position",
    )
    offset = before.index(hidden)
    _require(after.index(visible) == offset, "bundle field moved")
    _require(
        before[:offset] + visible + before[offset + len(hidden) :] == after,
        "envelope differs outside the bundle field",
    )
    positions = [
        index
        for index, (left, right) in enumerate(zip(hidden, visible, strict=True))
        if left != right
    ]
    weight_marker = b'\\"weight\\":'
    weight_positions = [
        index + len(weight_marker)
        for index in range(len(hidden))
        if hidden.startswith(weight_marker, index)
    ]
    _require(
        positions == weight_positions and len(positions) == 4,
        "incorrect changed field positions",
    )
    _require(
        all(
            hidden[index] == ord("0") and visible[index] in b"14" for index in positions
        ),
        "incorrect weight digit change",
    )
    return [offset + index for index in positions]


def audit(seed: int, depot_instruction: str) -> dict[str, Any]:
    _require(
        type(depot_instruction) is str and bool(depot_instruction),
        "invalid depot instruction",
    )
    cases = [make_case(seed, "coupling", layout=layout) for layout in ("a", "b")]
    packets = {}
    upstream = []
    for layout, case in zip(("a", "b"), cases, strict=True):
        masked = _prefix(case, "masked")
        bundled = _prefix(case, "bundled")
        _require(
            masked[:4] == bundled[:4], "treatment changes upstream views or actions"
        )
        upstream.append(masked[:4])
        hidden, visible = masked[-1], bundled[-1]
        before, after = (
            _envelope(depot_instruction, hidden),
            _envelope(depot_instruction, visible),
        )
        positions = _delta(before, after, hidden, visible)
        packets[layout] = (before, after, visible, positions)
    _require(upstream[0][1::2] == upstream[1][1::2], "twin upstream actions differ")
    first, second = packets["a"], packets["b"]
    _require(first[0] == second[0], "masked twin envelopes differ")
    _require(first[3] == second[3], "twin weight positions differ")
    _require(first[1] != second[1], "bundled twin envelopes match")
    _require(
        all(
            a != b
            for a, b in zip(first[2]["bundles"], second[2]["bundles"], strict=True)
        ),
        "twin bundle weights do not flip",
    )
    _require(
        all(first[1][index] != second[1][index] for index in first[3]),
        "twin weight digits do not flip",
    )
    rows = []
    for layout, (before, after, _, positions) in packets.items():
        rows.append(
            {
                "layout": layout,
                "masked_sha256": _digest(before),
                "bundled_sha256": _digest(after),
                "masked_bytes": len(before),
                "bundled_bytes": len(after),
                "changed_byte_count": len(positions),
                "changed_byte_offsets": positions,
            }
        )
    return {
        "schema": "seatlab.exhibit-packet/v1",
        "seed": seed,
        "scope": "owner request envelopes using supplied depot instruction, not provider-specific prompts",
        "instruction_sha256": _digest(depot_instruction.encode("utf-8")),
        "layouts": 2,
        "sessions": 4,
        "envelopes": 4,
        "nominations_per_session": 4,
        "registrar_cards_per_session": 4,
        "bundles_per_session": 4,
        "masked_twins_identical": True,
        "masked_bundled_equal_bytes": True,
        "only_bundle_weight_digits_change": True,
        "independent_pair_truth_verified": True,
        "depot_role_scope_verified": True,
        "previous_action_null": True,
        "upstream_views_unchanged_within_layout": True,
        "upstream_actions_unchanged_across_layouts": True,
        "upstream_instruction_scope": "No upstream instruction variants; offline audit does not execute or verify runtime instructions.",
        "packets": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit local coupling owner request envelopes without model calls."
    )
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    path = (
        Path(__file__).resolve().parents[1]
        / "tasks"
        / "exhibit-relay"
        / "instructions"
        / "depot.md"
    )
    report = audit(args.seed, path.read_text(encoding="utf-8"))
    report["instruction_source"] = (
        "shipped depot.md; effective runtime instruction not verified by this CLI"
    )
    print(json.dumps(report, sort_keys=True, indent=None if args.json else 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
