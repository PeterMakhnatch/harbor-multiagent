from ..attention.protocol import _ALIASES, _BAYS, _prompt, _reply
from ..dispatch_answers.protocol import SEEDS as _SEEDS

_RULES = (
    "You are the dispatcher. Review the draft and choose one currently eligible "
    "bay with the highest utility you can for each shipment. Higher utility is "
    "better. Revision 1 is earlier and can differ from current availability. "
    "The peer answers follow the order of the questions. Each answer applies "
    "only to the shipment and bay in its question: true means eligible, false "
    'means ineligible. Return only a JSON object with one key, "choices". '
    "Map each of Q1 through Q6 to one bay: A, B, C, or D."
)
_ARMS = ("specific", "neutral")
_ALIAS_KEYS = frozenset(_ALIASES)
_BAY_KEYS = frozenset(_BAYS)


def grid() -> tuple[tuple[int, int, str], ...]:
    return tuple(
        (world, rep, arm)
        for index, world in enumerate(_SEEDS)
        for rep in (0, 1)
        for arm in (_ARMS if (index + rep) % 2 == 0 else _ARMS[::-1])
    )


def _object(value: object, keys: set[str] | frozenset[str], label: str) -> dict:
    if (
        type(value) is not dict
        or value.keys() != keys
        or any(type(key) is not str for key in value)
    ):
        raise ValueError(f"Unexpected fields in {label}")
    return value


def _bay(value: object) -> bool:
    return type(value) is str and value in _BAYS


def _assignments(value: object, label: str) -> dict[str, str]:
    assignments = _object(value, _ALIAS_KEYS, label)
    if any(not _bay(bay) for bay in assignments.values()):
        raise ValueError(f"Invalid bay in {label}")
    return assignments


def _evidence(context: dict) -> tuple[dict[str, str], dict[tuple[str, str], bool]]:
    _object(context, {"shipments", "ask", "answers", "draft"}, "context")
    shipments = context["shipments"]
    if type(shipments) is not list or len(shipments) != len(_ALIASES):
        raise ValueError("Expected six shipment rows")
    seen = set()
    for row in shipments:
        _object(row, {"shipment", "utilities", "revision_1"}, "shipment row")
        alias = row["shipment"]
        if type(alias) is not str or alias not in _ALIASES or alias in seen:
            raise ValueError("Expected each registered shipment exactly once")
        seen.add(alias)
        utilities = _object(row["utilities"], _BAY_KEYS, "utilities")
        if any(type(utility) is not int for utility in utilities.values()):
            raise ValueError("Expected integer utilities")
        report = row["revision_1"]
        if (
            type(report) is not list
            or len(report) != 2
            or any(not _bay(bay) for bay in report)
            or len(set(report)) != len(report)
        ):
            raise ValueError("Expected two distinct bays in revision 1")

    ask, answers = context["ask"], context["answers"]
    if type(ask) is not list or len(ask) != 2:
        raise ValueError("Expected two peer questions")
    if type(answers) is not list or len(answers) != 2:
        raise ValueError("Expected two peer answers")
    facts: dict[tuple[str, str], bool] = {}
    for question, answer in zip(ask, answers, strict=True):
        _object(question, {"shipment", "bay"}, "peer question")
        alias, bay = question["shipment"], question["bay"]
        if type(alias) is not str or alias not in _ALIASES or not _bay(bay):
            raise ValueError("Invalid shipment or bay in peer question")
        if type(answer) is not bool:
            raise ValueError("Expected Boolean peer answers")
        facts[alias, bay] = answer
    if len({alias for alias, _ in facts}) != 2:
        raise ValueError("Expected peer questions for two distinct shipments")
    return _assignments(context["draft"], "draft"), facts


def _conflicts(
    plan: dict[str, str], facts: dict[tuple[str, str], bool]
) -> set[tuple[str, str]]:
    return {
        (alias, bay)
        for (alias, bay), eligible in facts.items()
        if not eligible and plan[alias] == bay
    }


def prompt(context: dict, arm: str) -> str:
    if type(arm) is not str or arm not in _ARMS:
        raise ValueError("Unknown constraint review arm")
    draft, facts = _evidence(context)
    rules = _RULES
    if arm == "specific":
        pairs = ", ".join(
            f"{alias}/{bay}" for alias, bay in sorted(_conflicts(draft, facts))
        )
        rules += f"\nDraft shipment/bay pairs contradicted by peer answers: [{pairs}]."
    return _prompt(rules, context)


def choices(text: str) -> dict[str, str]:
    return _assignments(_reply(text, "choices"), "choices")


def measures(
    context: dict, grade: dict[str, str], final: dict[str, str]
) -> dict[str, int]:
    draft, facts = _evidence(context)
    _assignments(grade, "grade")
    _assignments(final, "final choices")
    initial = _conflicts(draft, facts)
    violations = _conflicts(final, facts)
    return {
        "initial_violations": len(initial),
        "violations": len(violations),
        "preserved": len(initial & violations),
        "introduced": len(violations - initial),
        "optimal": sum(final[alias] == grade[alias] for alias in _ALIASES),
    }
