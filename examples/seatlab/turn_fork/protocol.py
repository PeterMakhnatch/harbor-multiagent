from typing import Any

from ..attention import protocol as attention
from ..briefing import protocol as briefing

_ARMS = ("aligned", "shifted", "repeat")


def arms(seed: int, rep: int) -> tuple[str, ...]:
    offset = (seed - briefing.SEEDS[0] + rep) % len(_ARMS)
    return _ARMS[offset:] + _ARMS[:offset]


def run_id(digest: str, source_id: str, arm: str, stage: str) -> str:
    return f"tf-{digest[:12]}-{source_id}-{arm}-{stage}"


def card_for(source: dict[str, Any], arm: str) -> str:
    if arm not in _ARMS:
        raise ValueError("Unknown turn-fork arm")
    card: str = source["card"]
    return card[1:] + card[:1] if arm == "shifted" else card


def push_prompt(source: dict[str, Any], arm: str) -> str:
    inventory = [
        {"id": case["id"], "reports": case["reports"]} for case in source["cases"]
    ]
    return briefing.push_prompt(
        inventory, source["seed"], source["repetition"], card_for(source, arm)
    )


def parse_push(text: str) -> tuple[str, str]:
    return briefing.parse_push(text)


def decide_prompt(source: dict[str, Any], opened: tuple[str, str]) -> str:
    dispatch = [
        {
            "id": case["id"],
            "utilities": case["utilities"],
            "messages": [
                {
                    "revision": report["revision"],
                    "available": sorted(report["available"]),
                }
                for report in reversed(case["reports"])
            ],
        }
        for case in source["cases"]
    ]
    return attention.decide_prompt(
        dispatch, source["seed"], source["repetition"], opened
    )


def grade(source: dict[str, Any], opened: tuple[str, str], text: str) -> dict[str, Any]:
    opened = attention._open_pair(opened)
    cases = source["cases"]
    seed, rep = source["seed"], source["repetition"]
    mapping = attention.aliases(cases, seed, rep)
    opened_ids = {mapping[alias] for alias in opened}
    choices = attention.decide(text, cases, seed, rep)
    by_id = {case["id"]: case for case in cases}
    actions: list[dict[str, Any]] = []
    opened_sensitive = 0
    for row in choices["rows"]:
        case_id, choice = row["id"], row["bay"]
        case = by_id[case_id]
        utilities: dict[str, int] = case["utilities"]
        after: list[str] = case["after"]
        actions.append(
            {
                "id": case_id,
                "choice": choice,
                "optimal": choice == max(after, key=utilities.__getitem__),
                "feasible": choice in after,
            }
        )
        if case_id in opened_ids:
            previous, current = case["reports"]
            before_rank = max(utilities[bay] for bay in previous["available"])
            after_rank = max(utilities[bay] for bay in current["available"])
            opened_sensitive += before_rank != after_rank
    return {
        "opened": list(opened),
        "actions": actions,
        "optimal": sum(action["optimal"] for action in actions),
        "feasible": sum(action["feasible"] for action in actions),
        "opened_sensitive": opened_sensitive,
    }
