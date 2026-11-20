import json
from copy import deepcopy

import pytest

from examples.seatlab.committee.contract import (
    ARMS,
    ROLES,
    share_action,
    share_prompt,
    vote_action,
    vote_prompt,
)

_OPTIONS = ("Option_A", "Option_B", "Option_C")
_NUMERIC_ROWS = {
    "s1": ("F_SHARED_1", "Option_B", 10),
    "s2": ("F_SHARED_2", "Option_B", 10),
    "s3": ("F_SHARED_3", "Option_B", 10),
    "s4": ("F_SHARED_4", "Option_A", 10),
    "s5": ("F_SHARED_5", "Option_C", 10),
    "p1": ("F_PRIV_1", "Option_B", -9),
    "p2": ("F_PRIV_2", "Option_A", 9),
    "p3": ("F_PRIV_3", "Option_B", -9),
    "p4": ("F_PRIV_4", "Option_A", 9),
}
_STOCK_ROLES = ("analyst_1", "analyst_2", "analyst_3", "analyst_4")
_NOVELTY_LINE = "Prefer a fact the other analysts cannot already see."
_MAPS = (
    (0, "XYZ", "s5 p1 p2 s2 p3 s1 p4 s4 s3"),
    (1, "XZY", "p4 s2 s1 s4 s3 p2 p1 p3 s5"),
    (2, "YXZ", "p1 s3 p3 p4 s5 s4 s1 p2 s2"),
    (3, "YZX", "s2 p3 s5 p4 p1 p2 s4 s3 s1"),
    (4, "ZXY", "p2 s4 s3 s1 s5 p1 p3 p4 s2"),
    (5, "ZYX", "s3 s5 s2 s4 p4 s1 p2 p1 p3"),
)
_POSTS = ("s2", "p2", "p3", "s2")


def _stock_row(key: str) -> dict:
    fact_id, option, points = _NUMERIC_ROWS[key]
    return {
        "id": fact_id,
        "kind": "stock-kind",
        "option": option,
        "points": points,
        "text": f"Unblinded narrative for {fact_id} and {option}",
    }


def _dossier(seat: int) -> dict:
    return {
        "seat": _STOCK_ROLES[seat - 1],
        "options": list(_OPTIONS),
        "rubric": "Unblinded rubric with the stock world names",
        "shared_facts": [_stock_row(f"s{i}") for i in range(1, 6)],
        "private_facts": [_stock_row(f"p{seat}")],
    }


def _stock_post(key: str) -> str:
    fact_id, option, points = _NUMERIC_ROWS[key]
    return f'{{"id":"{fact_id}","option":"{option}","points":{points}}}'


@pytest.fixture
def dossier() -> dict:
    return _dossier(1)


@pytest.fixture
def chat() -> dict:
    messages = [
        {"seat": role, "text": _stock_post(key), "seq": seq}
        for seq, (role, key) in enumerate(zip(_STOCK_ROLES, _POSTS, strict=True), 101)
    ]
    return {"messages": messages, "unique_speakers": list(_STOCK_ROLES)}


def _section(prompt: str, name: str):
    prefix = f"{name}: "
    (line,) = (line for line in prompt.splitlines() if line.startswith(prefix))
    return json.loads(line.removeprefix(prefix))


def _scores(rows: list[dict]) -> dict[str, int]:
    scores = {label: 0 for label in "XYZ"}
    for row in rows:
        scores[row["option"]] += row["points"]
    return scores


@pytest.mark.parametrize(("map_index", "labels", "fact_order"), _MAPS)
def test_blinded_projection_and_native_actions(
    dossier: dict, chat: dict, map_index: int, labels: str, fact_order: str
) -> None:
    assert ARMS == ("open", "novelty")
    assert ROLES == _STOCK_ROLES
    options = dict(zip(_OPTIONS, labels, strict=True))
    aliases = {key: f"K{i}" for i, key in enumerate(fact_order.split(), 1)}

    def project(keys: tuple[str, ...]) -> list[dict]:
        return [
            {
                "fact": aliases[key],
                "option": options[_NUMERIC_ROWS[key][1]],
                "points": _NUMERIC_ROWS[key][2],
                "provenance": "private" if key.startswith("p") else "shared",
            }
            for key in fact_order.split()
            for _ in range(keys.count(key))
        ]

    open_prompt = share_prompt(dossier, map_index, "open")
    novelty_prompt = share_prompt(dossier, map_index, "novelty")
    assert novelty_prompt.splitlines().count(_NOVELTY_LINE) == 1
    assert [line for line in novelty_prompt.splitlines() if line != _NOVELTY_LINE] == (
        open_prompt.splitlines()
    )
    expected_dossier = {
        "options": ["X", "Y", "Z"],
        "facts": project(("s1", "s2", "s3", "s4", "s5", "p1")),
    }
    assert _section(open_prompt, "Dossier") == expected_dossier
    for key in ("p1", "s2"):
        assert share_action(json.dumps({"fact": aliases[key]}), dossier, map_index) == {
            "text": _stock_post(key)
        }

    prompt = vote_prompt(dossier, chat, map_index)
    assert _section(prompt, "Dossier") == expected_dossier
    assert _NOVELTY_LINE not in prompt
    assert "Count that fact only once." in prompt
    for hidden in (
        *_OPTIONS,
        *_STOCK_ROLES,
        "F_SHARED_",
        "F_PRIV_",
        "Unblinded",
        "stock-kind",
    ):
        assert hidden not in open_prompt
        assert hidden not in prompt

    submissions = _section(prompt, "Submissions")
    assert submissions == project(_POSTS)
    assert len(submissions) == 4
    assert sum(row["fact"] == aliases["s2"] for row in submissions) == 2
    assert (
        vote_prompt(
            {**dossier, "shared_facts": dossier["shared_facts"][::-1]},
            {
                "messages": chat["messages"][::-1],
                "unique_speakers": chat["unique_speakers"][::-1],
            },
            map_index,
        )
        == prompt
    )

    # The dossier alone favors B. Three private facts reverse the result only
    # when repeated reports and their dossier copy count as one fact.
    own_rows = _section(prompt, "Dossier")["facts"]
    assert _scores(own_rows) == dict(zip(labels, (10, 21, 10), strict=True))
    rows = own_rows + submissions
    assert _scores(rows) == dict(zip(labels, (19, 32, 10), strict=True))
    unique = {row["fact"]: row for row in rows}
    assert _scores(list(unique.values())) == dict(zip(labels, (19, 12, 10), strict=True))
    assert aliases["p4"] not in unique

    for native_option in _OPTIONS:
        text = json.dumps(
            {"choice": options[native_option], "cited": [aliases["p3"], aliases["p1"]]}
        )
        assert vote_action(text, dossier, chat, map_index) == {
            "choice": native_option,
            "cited_facts": ["F_PRIV_3", "F_PRIV_1"],
        }
    assert vote_action(
        json.dumps({"choice": options["Option_A"], "cited": [aliases["s2"]]}),
        dossier,
        chat,
        map_index,
    )["cited_facts"] == ["F_SHARED_2"]
    with pytest.raises(ValueError):
        vote_action(
            json.dumps({"choice": options["Option_A"], "cited": [aliases["p4"]]}),
            dossier,
            chat,
            map_index,
        )


@pytest.mark.parametrize(("seat", "alias"), [(1, "K2"), (2, "K3"), (3, "K5"), (4, "K7")])
def test_each_seat_can_share_its_own_private_fact(seat: int, alias: str) -> None:
    assert share_action(json.dumps({"fact": alias}), _dossier(seat), 0) == {
        "text": _stock_post(f"p{seat}")
    }


@pytest.mark.parametrize(
    "text",
    [
        '{"fact":"K3"}',  # A valid alias absent from this dossier.
        '{"fact":"K10"}',
        '{"fact":"F_PRIV_1"}',
        '{"fact":true}',
        '{"fact":null}',
        '{"fact":NaN}',
        '{"fact":["K2"]}',
        '{"fact":"K4","fact":"K2"}',
        '{"fact":"K2","facts":["K2"]}',
        '{}',
        '```json\n{"fact":"K2"}\n```',
        '{"fact":"K2"}{}',
    ],
)
def test_rejects_invalid_share_response(dossier: dict, text: str) -> None:
    with pytest.raises(ValueError):
        share_action(text, dossier, 0)


@pytest.mark.parametrize(
    "text",
    [
        '{"choice":"X","cited":["K2","K2"]}',
        '{"choice":"X","cited":["K7"]}',
        '{"choice":"X","cited":["K10"]}',
        '{"choice":"X","cited":["F_PRIV_1"]}',
        '{"choice":"X","cited":[true]}',
        '{"choice":"X","cited":[["K2"]]}',
        '{"choice":"X","cited":[]}',
        '{"choice":"X","cited":"K2"}',
        '{"choice":"X","cited":["K2"],"cited":["K3"]}',
        '{"choice":"X","choice":"Y","cited":["K2"]}',
        '{"choice":"X","cited":[Infinity]}',
        '{"choice":"Option_A","cited":["K2"]}',
        '{"choice":false,"cited":["K2"]}',
        '{"choice":"X"}',
        '{"choice":"X","cited":["K2"],"extra":null}',
        '[{"choice":"X","cited":["K2"]}]',
    ],
)
def test_rejects_invalid_vote_response(dossier: dict, chat: dict, text: str) -> None:
    with pytest.raises(ValueError):
        vote_action(text, dossier, chat, 0)


@pytest.mark.parametrize("points", [True, 10.0, float("nan"), float("inf"), "10"])
def test_rejects_noninteger_dossier_points(dossier: dict, points: object) -> None:
    dossier["shared_facts"][0]["points"] = points
    with pytest.raises(ValueError):
        share_prompt(dossier, 0, "open")


def test_rejects_absent_or_duplicate_dossier_rows(dossier: dict) -> None:
    missing = deepcopy(dossier)
    missing["shared_facts"].pop()
    duplicate = deepcopy(dossier)
    duplicate["shared_facts"][0] = duplicate["shared_facts"][1].copy()
    conflicting = deepcopy(duplicate)
    conflicting["shared_facts"][0]["points"] = -10
    wrong_private = deepcopy(dossier)
    wrong_private["private_facts"] = [_stock_row("p2")]
    for invalid in (missing, duplicate, conflicting, wrong_private):
        with pytest.raises(ValueError):
            share_prompt(invalid, 0, "open")
        with pytest.raises(ValueError):
            share_action('{"fact":"K2"}', invalid, 0)


@pytest.mark.parametrize(
    "text",
    [
        '{"id":"F_SHARED_2","option":"Option_B","points":true}',
        '{"id":"F_SHARED_2","option":"Option_B","points":10.0}',
        '{"id":"F_SHARED_2","option":"Option_B","points":NaN}',
        '{"id":"F_SHARED_2","option":"Option_B","points":1e999}',
        '{"id":"F_SHARED_2","option":"Option_B","points":9,"points":10}',
        '{"id":"F_SHARED_2","option":"Option_A","points":10}',
        '{"id":"F_SHARED_2","option":"Option_B","points":-10}',
        '{"id":"F_SHARED_2","option":"Y","points":10}',
        '{"id":"F_SHARED_6","option":"Option_B","points":10}',
        '{"id":"F_PRIV_2","option":"Option_A","points":9}',
        '{"id":"F_PRIV_1","option":"Option_B","points":-10}',
        '{"id":"F_SHARED_2","option":"Option_B"}',
        '{"id":"F_SHARED_2","option":"Option_B","points":10,"text":"narrative"}',
        'Option_B gets 10 points',
    ],
)
def test_rejects_invalid_native_chat_fact(dossier: dict, chat: dict, text: str) -> None:
    chat["messages"][0]["text"] = text
    with pytest.raises(ValueError):
        vote_prompt(dossier, chat, 0)
    with pytest.raises(ValueError):
        vote_action('{"choice":"X","cited":["K2"]}', dossier, chat, 0)


def test_rejects_incomplete_or_ambiguous_chat_snapshot(dossier: dict, chat: dict) -> None:
    missing = deepcopy(chat)
    missing["messages"].pop()
    duplicate = deepcopy(chat)
    duplicate["messages"][1] = {**duplicate["messages"][0], "seq": 123}
    duplicate_seq = deepcopy(chat)
    duplicate_seq["messages"][1]["seq"] = duplicate_seq["messages"][0]["seq"]
    invalid_seq = deepcopy(chat)
    invalid_seq["messages"][0]["seq"] = True
    inconsistent_speakers = deepcopy(chat)
    inconsistent_speakers["unique_speakers"] = [*_STOCK_ROLES[:3], _STOCK_ROLES[0]]
    for invalid in (missing, duplicate, duplicate_seq, invalid_seq, inconsistent_speakers):
        with pytest.raises(ValueError):
            vote_prompt(dossier, invalid, 0)


@pytest.mark.parametrize("map_index", [-1, 6, True, 1.0])
def test_rejects_invalid_map(dossier: dict, chat: dict, map_index: int) -> None:
    with pytest.raises(ValueError):
        share_prompt(dossier, map_index, "open")
    with pytest.raises(ValueError):
        share_action('{"fact":"K2"}', dossier, map_index)
    with pytest.raises(ValueError):
        vote_prompt(dossier, chat, map_index)
    with pytest.raises(ValueError):
        vote_action('{"choice":"X","cited":["K2"]}', dossier, chat, map_index)


def test_rejects_invalid_arm(dossier: dict) -> None:
    with pytest.raises(ValueError):
        share_prompt(dossier, 0, "unknown")
