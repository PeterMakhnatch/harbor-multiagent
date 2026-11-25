import argparse
import json
from importlib import import_module
from itertools import combinations
from pathlib import Path
from typing import Any

from examples.seatlab.exhibit_wire import encode_request, render_turn

TASK = "examples.tasks.exhibit-relay"
WORLD = f"{TASK}.environment.exhibit_relay"
SCHEMA = "seatlab.exhibit-authored/v1"
MODES = ("authored", "author-masked")
LAYOUTS = ("a", "b")
CASES = ("true", "omitted", "wrong", "empty", "segmented", "abstained")
INSTRUCTION = (
    Path(__file__).resolve().parents[1] / "tasks/exhibit-relay/instructions/depot.md"
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def pair(row: dict[str, Any]) -> tuple[int, str, str]:
    return (row["epoch"], *row["keys"])


def withheld(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**row, "weight": 0} for row in notes]


def variant(notes: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    require(
        len(notes) == 4 and sorted(row["weight"] for row in notes) == [1, 1, 4, 4],
        "unexpected source weights",
    )
    if kind == "true":
        return notes
    if kind == "omitted":
        return notes[1:]
    if kind == "wrong":
        return [{**notes[0], "weight": 9}, *notes[1:]]
    if kind == "empty":
        return []
    if kind == "segmented":
        return [row for row in notes if row["weight"] == 4]
    if kind == "abstained":
        return [{**notes[0], "weight": 0}, *notes[1:]]
    raise ValueError("unknown authoring case")


def at(cards: list[dict[str, Any]], key: str, epoch: int) -> dict[str, Any]:
    matches = [
        card
        for card in cards
        if card["key"] == key and card["start"] <= epoch <= card["end"]
    ]
    require(len(matches) == 1, "nonunique source mapping")
    return matches[0]


def source_truth(
    case: dict[str, Any], slips: list[str], ids: list[str]
) -> list[dict[str, Any]]:
    truth = []
    for brief in case["briefs"]:
        if brief["id"] not in slips:
            continue
        epoch = brief["epoch"]
        sources = [at(case["registrar"], item, epoch) for item in brief["items"]]
        if all(card["id"] in ids for card in sources):
            truth.append(
                {
                    "epoch": epoch,
                    "keys": sorted(card["value"] for card in sources),
                    "weight": brief["weight"],
                }
            )
    return sorted(truth, key=pair)


def prefix(case: dict[str, Any], mode: str, kind: str) -> dict[str, Any]:
    session = import_module(f"{WORLD}.engine").Session(case, mode)
    act = import_module(f"{TASK}.solution.policies").act
    audit_notes = import_module(f"{WORLD}.notes").audit_notes
    loader = session.view("loader")
    request = act(loader, "portfolio")
    session.apply("loader", request)
    registrar = session.view("registrar")
    filing = act(registrar, "portfolio")
    truth = source_truth(case, request["slips"], filing["ids"])
    require(
        filing["notes"] == truth,
        "reference claims differ from independent source mapping",
    )
    notes = variant(truth, kind)
    session.apply("registrar", {**filing, "notes": notes})
    accepted = session.snapshot()["events"][1]["action"]
    require(
        accepted["notes"] == notes and accepted["ids"] == filing["ids"],
        "submission changed",
    )
    depot = session.view("depot")
    require(
        depot["quota"] == 2 and len(request["slips"]) == len(filing["ids"]) == 4,
        "unexpected fixed prefix",
    )
    return {
        "loader": loader,
        "request": request,
        "registrar": registrar,
        "file_ids": accepted["ids"],
        "submitted": accepted["notes"],
        "truth": truth,
        "depot": depot,
        "delivered": depot["bundles"],
        "depot_base": {key: value for key, value in depot.items() if key != "bundles"},
        "audit": audit_notes(
            case, request["slips"], accepted["ids"], accepted["notes"]
        ),
    }


def check_audit(result: dict[str, Any], kind: str) -> dict[str, int]:
    truth = {pair(row): row["weight"] for row in result["truth"]}
    claims = {pair(row): row["weight"] for row in result["submitted"]}
    matched = truth.keys() & claims.keys()
    expected = {
        "assertion_count": len(claims),
        "true_pair_count": len(truth),
        "correct_pair_count": len(matched),
        "irrelevant_pair_count": len(claims.keys() - truth.keys()),
        "missing_true_pair_count": len(truth.keys() - claims.keys()),
        "accurate_positive_weight_count": sum(claims[p] == truth[p] for p in matched),
        "inaccurate_positive_weight_count": sum(
            0 < claims[p] != truth[p] for p in matched
        ),
        "unknown_correct_weight_count": sum(claims[p] == 0 for p in matched),
        "unknown_weight_count": sum(w == 0 for w in claims.values()),
        "irrelevant_positive_weight_count": sum(
            claims[p] > 0 for p in claims.keys() - truth.keys()
        ),
    }
    require(
        all(result["audit"][key] == value for key, value in expected.items()),
        "source audit disagrees with independent mapping",
    )
    count, wrong, unknown = {
        "true": (4, 0, 0),
        "omitted": (3, 0, 0),
        "wrong": (4, 1, 0),
        "empty": (0, 0, 0),
        "segmented": (2, 0, 0),
        "abstained": (4, 0, 1),
    }[kind]
    require(
        expected["correct_pair_count"] == count
        and expected["missing_true_pair_count"] == 4 - count,
        "incorrect pair fixture",
    )
    require(
        expected["inaccurate_positive_weight_count"] == wrong
        and expected["unknown_weight_count"] == unknown,
        "incorrect weight fixture",
    )
    require(
        expected["accurate_positive_weight_count"] == count - wrong - unknown,
        "incorrect faithful weight count",
    )
    return expected


def packet(instruction: str, view: dict[str, Any]) -> bytes:
    ordered = json.loads(canonical(view))
    prompt = render_turn(instruction, ordered, None)
    _, marker, payload = prompt.partition("\nInput JSON: ")
    require(
        bool(marker)
        and json.loads(payload)
        == {"instruction": instruction, "view": ordered, "previous_action": None},
        "owner payload mismatch",
    )
    wire = encode_request(prompt)
    require(
        wire.endswith(b"\n")
        and json.loads(wire) == {"schema": "seatlab.seat-turn/v1", "prompt": prompt},
        "owner envelope mismatch",
    )
    return wire


def compare(
    left: dict[str, Any], right: dict[str, Any], kind: str, instruction: str
) -> list[dict[str, Any]]:
    for field in (
        "loader",
        "request",
        "registrar",
        "file_ids",
        "submitted",
        "truth",
        "depot_base",
        "audit",
    ):
        require(
            canonical(left[field]) == canonical(right[field]), f"arm mismatch: {field}"
        )
    submitted = left["submitted"]
    require(left["delivered"] == submitted, "authored delivery changed")
    require(right["delivered"] == withheld(submitted), "mask changed beyond weight")
    authored_bytes = packet(instruction, left["depot"])
    masked_bytes = packet(instruction, right["depot"])
    expected_bytes = packet(
        instruction, {**left["depot"], "bundles": withheld(submitted)}
    )
    require(masked_bytes == expected_bytes, "nonweight envelope fields changed")
    require(len(authored_bytes) == len(masked_bytes), "unequal full request lengths")
    changes = [
        (a, b) for a, b in zip(authored_bytes, masked_bytes, strict=True) if a != b
    ]
    require(
        len(changes) == sum(row["weight"] > 0 for row in submitted),
        "unexpected number of changed bytes",
    )
    require(
        all(49 <= a <= 57 and b == 48 for a, b in changes), "nonweight digit changed"
    )
    rows = []
    for mode, result in zip(MODES, (left, right), strict=True):
        delivered = result["delivered"]
        visible = sum(row["weight"] > 0 for row in delivered)
        rows.append(
            {
                "mode": mode,
                "case": kind,
                "submitted_source_audit": check_audit(result, kind),
                "delivered_notes_count": len(delivered),
                "delivered_positive_weight_count": visible,
                "delivered_unknown_weight_count": len(delivered) - visible,
                "owner_request_utf8_bytes": len(authored_bytes),
                "weight_only_byte_changes": len(changes),
                "donor_views_identical": True,
                "non_claim_depot_view_identical": True,
            }
        )
    return rows


def requirements(result: dict[str, Any]) -> list[tuple[frozenset[str], int]]:
    cards = result["depot"]["cards"]
    return [
        (
            frozenset(at(cards, key, row["epoch"])["id"] for key in row["keys"]),
            row["weight"],
        )
        for row in result["truth"]
    ]


def coverage(needed: list[tuple[frozenset[str], int]], selected: frozenset[str]) -> int:
    return sum(weight for ids, weight in needed if ids <= selected)


def bounds(
    full: list[dict[str, Any]], selected: list[dict[str, Any]], instruction: str
) -> dict[str, Any]:
    act = import_module(f"{TASK}.solution.policies").act
    require(
        full[0]["depot_base"]
        == full[1]["depot_base"]
        == selected[0]["depot_base"]
        == selected[1]["depot_base"],
        "twin nonclaim views differ",
    )
    require(
        packet(instruction, full[0]["depot"]) == packet(instruction, full[1]["depot"]),
        "full masked twin packets differ",
    )
    pairs = [{pair(row) for row in result["delivered"]} for result in selected]
    require(
        all(len(p) == 2 for p in pairs) and pairs[0].isdisjoint(pairs[1]),
        "selected-heavy pairs do not distinguish twins",
    )
    require(
        packet(instruction, selected[0]["depot"])
        != packet(instruction, selected[1]["depot"]),
        "selected-heavy masked packets coincide",
    )
    cards = sorted(card["id"] for card in full[0]["depot"]["cards"])
    require(
        8 <= len(cards) <= 24 and len(set(cards)) == len(cards), "invalid depot archive"
    )
    legal = [frozenset(ids) for size in range(3) for ids in combinations(cards, size)]
    needed = [requirements(result) for result in full]
    require(
        needed == [requirements(result) for result in selected],
        "selection changed source requirements",
    )
    total = sum(weight for layout in needed for _, weight in layout)
    common = max(sum(coverage(layout, ids) for layout in needed) for ids in legal)
    informed = sum(max(coverage(layout, ids) for ids in legal) for layout in needed)
    achieved = 0
    for result, layout in zip(selected, needed, strict=True):
        action = act(result["depot"], "portfolio")
        ids = frozenset(action["ids"])
        require(
            action["op"] == "file" and len(ids) == len(action["ids"]) and ids in legal,
            "illegal depot selection",
        )
        achieved += coverage(layout, ids)
    require(
        (common, informed, achieved, total) == (5, 8, 8, 20),
        "unexpected coverage bounds",
    )
    return {
        "full_masked_common_file_weight_bound": common,
        "layout_informed_file_weight_bound": informed,
        "selected_heavy_masked_policy_weight": achieved,
        "total_forecast_weight": total,
        "legal_file_count": len(legal),
        "pair_selection_distinguishes_twins": True,
    }


def certify(seed: int, depot_instruction: str | None = None) -> dict[str, Any]:
    instruction = (
        INSTRUCTION.read_text(encoding="utf-8")
        if depot_instruction is None
        else depot_instruction
    )
    require(type(seed) is int and 0 <= seed < 2**63, "invalid development seed")
    require(
        type(instruction) is str and bool(instruction.strip()),
        "missing depot instruction",
    )
    make_case = import_module(f"{WORLD}.cases").make_case
    rows = []
    full, selected = [], []
    for layout in LAYOUTS:
        case = make_case(seed, "coupling", layout=layout)
        for kind in CASES:
            left, right = (prefix(case, mode, kind) for mode in MODES)
            rows.extend(
                {"layout": layout, **row}
                for row in compare(left, right, kind, instruction)
            )
            if kind == "true":
                full.append(right)
            elif kind == "segmented":
                selected.append(right)
    return {
        "schema": SCHEMA,
        "development_only": True,
        "model_calls": 0,
        "seed": seed,
        "row_count": len(rows),
        "certificate_rows": rows,
        "nondisclosure_case_count": len(rows) // 2,
        **bounds(full, selected, instruction),
        "scope": "Development source fidelity and transport only. Bounds assume fixed complete upstream filings. No model or realized reward claim.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline authored-note development certificate"
    )
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--instruction", type=Path, default=INSTRUCTION)
    args = parser.parse_args(argv)
    report = certify(args.seed, args.instruction.read_text(encoding="utf-8"))
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":") if args.json else None,
            indent=None if args.json else 2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
