from typing import Any

from ..attention.protocol import _ALIASES, _BAYS, _prompt, _reply
from ..briefing.protocol import _ordered_cases, _report_sets

SEEDS = tuple(range(6901, 6909))
ARMS = ("visible", "withheld")
EPISODES = 6
CONDITION = "stale"
AGENT_NAME = "seatlab-dispatch-answers"
AGENT_IMPORT = "examples.seatlab.dispatch_answers.agent:DispatchAnswersSeat"
SEAT_SCHEMA = "seatlab.native-dispatch-answers-seat/v1"
STAGES = (
    ("inventory", "inventory"),
    ("question", "dispatch"),
    ("answer", "inventory"),
    ("decide", "dispatch"),
)

type Choice = tuple[str, str]
type Question = tuple[Choice, Choice]
type Answers = tuple[bool, bool]

_WITHHELD_ACK = ("Q5", "Q6")
_FIRST_BITS = ("Q1", "Q2")
_SECOND_BITS = ("Q3", "Q4")

_QUESTION_RULES = (
    "You are the dispatcher. For each shipment you have bay utilities "
    "and an earlier availability report. Higher utility is better. "
    "The current availability can differ. Ask exactly two questions, each about "
    "whether one specific bay is currently eligible for one specific shipment. "
    "Choose different shipments for the two questions. Choose the questions "
    "whose answers would most help you pick eligible high-utility bays. "
    'Return only {"ask":[{"shipment":"Q1","bay":"A"},'
    '{"shipment":"Q2","bay":"B"}]}.'
)
_ANSWER_RULES = (
    "You are inventory. These are your own submitted availability reports "
    "and the dispatcher's two questions. Use revision_2 for current eligibility. "
    "For each question, give true if the specific bay is listed for that "
    "shipment in revision_2, otherwise false. Keep the question order. "
    'Return only two JSON Booleans in {"answers":[true,false]}.'
)
_DECIDE_RULES = (
    "You are the dispatcher. Choose one currently eligible bay with the highest "
    "utility you can for each shipment. Higher utility is better. The revision_1 "
    "availability is earlier and can differ from current availability. "
    "Use any current eligibility answers for your questions in their given order. "
    "If an answer is absent, make your best decision with the information you have. "
    "Each answer is only about its specified shipment and bay. "
    'Return only {"choices":{"Q1":"A","Q2":"B","Q3":"C","Q4":"D","Q5":"A","Q6":"B"}} '
    "with one bay per shipment."
)


def grid() -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "cell_id": f"danswer-s{seed}-{arm}",
            "seed": seed,
            "repetition": 0,
            "condition": CONDITION,
            "pairing": "MM",
            "arm": arm,
        }
        for index, seed in enumerate(SEEDS)
        for arm in (ARMS if index % 2 == 0 else ARMS[::-1])
    )


def question_rows(question: Question) -> list[dict[str, str]]:
    return [{"shipment": shipment, "bay": bay} for shipment, bay in question]


def _question(value: object) -> Question:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("Expected exactly two eligibility questions")
    items = []
    for row in value:
        if not isinstance(row, dict) or row.keys() != {"shipment", "bay"}:
            raise ValueError("Expected a shipment and bay in each question")
        shipment, bay = row["shipment"], row["bay"]
        if shipment not in _ALIASES or bay not in _BAYS:
            raise ValueError("Expected a registered shipment alias and bay")
        items.append((shipment, bay))
    first, second = items
    if first[0] == second[0]:
        raise ValueError("Questions must use different shipments")
    return first, second


def parse_question(text: str) -> Question:
    return _question(_reply(text, "ask"))


def parse_answers(text: str) -> Answers:
    value = _reply(text, "answers")
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(type(answer) is not bool for answer in value)
    ):
        raise ValueError("Expected exactly two Boolean eligibility answers")
    return value[0], value[1]


def question_to_wire(question: Question) -> str:
    parts = []
    for shipment, bay in question:
        high, low = divmod(_ALIASES.index(shipment), len(_BAYS))
        parts.extend((_BAYS[high], _BAYS[low], bay))
    return "".join(parts)


def question_from_wire(wire: str | None) -> Question:
    if wire is None or len(wire) != 6 or any(digit not in _BAYS for digit in wire):
        raise ValueError("Invalid question on the scoped channel")
    rows = []
    for offset in (0, 3):
        index = len(_BAYS) * _BAYS.index(wire[offset]) + _BAYS.index(wire[offset + 1])
        if index >= EPISODES:
            raise ValueError("Invalid shipment alias on the scoped channel")
        rows.append({"shipment": _ALIASES[index], "bay": wire[offset + 2]})
    return _question(rows)


def answers_to_wire(answers: Answers, visible: bool) -> tuple[str, str]:
    if not visible:
        return _WITHHELD_ACK
    return _FIRST_BITS[answers[0]], _SECOND_BITS[answers[1]]


def answers_from_wire(wire: tuple[str, str], visible: bool) -> Answers | None:
    if not visible and wire == _WITHHELD_ACK:
        return None
    if visible and wire[0] in _FIRST_BITS and wire[1] in _SECOND_BITS:
        return wire[0] == _FIRST_BITS[1], wire[1] == _SECOND_BITS[1]
    raise ValueError("Invalid answer on the scoped channel")


def _dispatch_rows(cases: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    rows = []
    for alias, case in _ordered_cases(cases, seed, 0):
        (report,) = case["messages"]
        if report["revision"] != 1 or case["pooled_available"] is not None:
            raise ValueError("Dispatch may receive revision 1 only")
        rows.append(
            {
                "shipment": alias,
                "utilities": case["utilities"],
                "revision_1": report["available"],
            }
        )
    return rows


def question_prompt(cases: list[dict[str, Any]], seed: int) -> str:
    return _prompt(_QUESTION_RULES, {"shipments": _dispatch_rows(cases, seed)})


def answer_prompt(
    submitted: list[dict[str, Any]], seed: int, question: Question
) -> str:
    rows = []
    for alias, case in _ordered_cases(submitted, seed, 0):
        reports = _report_sets(case["reports"])
        rows.append(
            {"shipment": alias, "revision_1": reports[1], "revision_2": reports[2]}
        )
    return _prompt(_ANSWER_RULES, {"shipments": rows, "ask": question_rows(question)})


def decide_prompt(
    cases: list[dict[str, Any]], seed: int, question: Question, answers: Answers | None
) -> str:
    packet: dict[str, Any] = {
        "shipments": _dispatch_rows(cases, seed),
        "ask": question_rows(question),
    }
    if answers is not None:
        packet["answers"] = list(answers)
    return _prompt(_DECIDE_RULES, packet)
