from typing import Any, cast

Pair = tuple[int, str, str]


def normalize_notes(raw: object, crates: list[str]) -> list[dict[str, Any]]:
    if type(raw) is not list or len(raw) > 4:
        raise ValueError("notes must be a list of at most four assertions")
    result: list[dict[str, Any]] = []
    seen: set[Pair] = set()
    for row in cast("list[dict[str, Any]]", raw):
        if type(row) is not dict or row.keys() != {"epoch", "keys", "weight"}:
            raise ValueError("invalid assertion fields")
        epoch, keys, weight = row["epoch"], row["keys"], row["weight"]
        if type(epoch) is not int or not 0 <= epoch <= 2:
            raise ValueError("invalid assertion epoch")
        if type(weight) is not int or not 0 <= weight <= 9:
            raise ValueError("invalid assertion weight")
        if type(keys) is not list or len(keys) != 2:
            raise ValueError("an assertion requires two crate labels")
        if (
            any(type(key) is not str or key not in crates for key in keys)
            or keys[0] == keys[1]
        ):
            raise ValueError("assertion requires distinct public crate labels")
        left, right = sorted(keys)
        pair = (epoch, left, right)
        if pair in seen:
            raise ValueError("duplicate assertion pair")
        seen.add(pair)
        result.append({"epoch": epoch, "keys": [left, right], "weight": weight})
    return sorted(result, key=lambda row: (row["epoch"], row["keys"]))


def deliver_notes(
    notes: list[dict[str, Any]], *, disclose: bool
) -> list[dict[str, Any]]:
    if type(disclose) is not bool:
        raise ValueError("disclose must be a boolean")
    return [
        {
            "epoch": row["epoch"],
            "keys": list(row["keys"]),
            "weight": row["weight"] if disclose else 0,
        }
        for row in notes
    ]


def _true_pairs(
    case: dict[str, Any], slips: list[str], registrar_ids: list[str]
) -> tuple[dict[Pair, int], set[Pair]]:
    nominated = set(slips)
    filed = set(registrar_ids)
    true_pairs: dict[Pair, int] = {}
    documented: set[Pair] = set()
    for brief in case["briefs"]:
        if brief["id"] not in nominated:
            continue
        epoch = brief["epoch"]
        sources = []
        for item in brief["items"]:
            matches = [
                card
                for card in case["registrar"]
                if card["key"] == item and card["start"] <= epoch <= card["end"]
            ]
            if len(matches) != 1:
                raise ValueError("invalid registrar epoch mapping")
            sources.append(matches[0])
        left, right = sorted(card["value"] for card in sources)
        pair = (epoch, left, right)
        if left == right or pair in true_pairs:
            raise ValueError("invalid coupling pair structure")
        true_pairs[pair] = brief["weight"]
        if all(card["id"] in filed for card in sources):
            documented.add(pair)
    return true_pairs, documented


def audit_notes(
    case: dict[str, Any],
    slips: list[str],
    registrar_ids: list[str],
    notes: list[dict[str, Any]],
) -> dict[str, Any]:
    if case["profile"] != "coupling":
        raise ValueError("notes audit requires a coupling case")
    normalized = normalize_notes(notes, case["crates"])
    truth, documented = _true_pairs(case, slips, registrar_ids)
    claims = {(row["epoch"], *row["keys"]): row["weight"] for row in normalized}
    correct = truth.keys() & claims.keys()
    irrelevant = claims.keys() - truth.keys()
    return {
        "assertion_count": len(claims),
        "true_pair_count": len(truth),
        "correct_pair_count": len(correct),
        "irrelevant_pair_count": len(irrelevant),
        "missing_true_pair_count": len(truth.keys() - claims.keys()),
        "accurate_positive_weight_count": sum(
            0 < claims[pair] == truth[pair] for pair in correct
        ),
        "inaccurate_positive_weight_count": sum(
            claims[pair] > 0 and claims[pair] != truth[pair] for pair in correct
        ),
        "unknown_correct_weight_count": sum(claims[pair] == 0 for pair in correct),
        "unknown_weight_count": sum(weight == 0 for weight in claims.values()),
        "irrelevant_positive_weight_count": sum(
            claims[pair] > 0 for pair in irrelevant
        ),
        "documented_true_pair_count": len(documented),
        "claimed_documented_pair_count": len(correct & documented),
        "claimed_unfiled_true_pair_count": len(correct - documented),
        "missing_documented_true_pair_count": len(documented - claims.keys()),
    }
