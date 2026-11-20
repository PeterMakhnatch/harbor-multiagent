import json
from hashlib import sha256

import pytest

from examples.seatlab.committee.contract import (
    tally_action,
    tally_prompt,
    vote_action,
    vote_prompt,
)


@pytest.fixture
def evidence() -> tuple[dict, dict]:
    options = ("Option_A", "Option_B", "Option_C")
    roles = [f"analyst_{i}" for i in range(1, 5)]
    rows = {
        f"F_SHARED_{i}": (options[index], 10)
        for i, index in enumerate((1, 1, 1, 0, 2), 1)
    }
    rows.update(
        {
            "F_PRIV_1": ("Option_B", -9),
            "F_PRIV_2": ("Option_A", 9),
            "F_PRIV_3": ("Option_B", -9),
        }
    )

    def fact(fact_id: str) -> dict:
        option, points = rows[fact_id]
        return {"id": fact_id, "option": option, "points": points}

    dossier = {
        "seat": roles[0],
        "options": list(options),
        "rubric": "Unblinded rubric",
        "shared_facts": [
            dict(fact(f"F_SHARED_{i}"), kind="stock", text="Unblinded narrative")
            for i in range(1, 6)
        ],
        "private_facts": [
            dict(fact("F_PRIV_1"), kind="stock", text="Unblinded narrative")
        ],
    }
    posts = ("F_SHARED_2", "F_PRIV_2", "F_PRIV_3", "F_SHARED_2")
    chat = {
        "messages": [
            {"seat": role, "text": json.dumps(fact(fact_id)), "seq": i}
            for i, (role, fact_id) in enumerate(zip(roles, posts, strict=True), 1)
        ],
        "unique_speakers": roles,
    }
    return dossier, chat


@pytest.mark.parametrize("map_index", range(6))
def test_tally_changes_only_the_response_format(
    evidence: tuple[dict, dict], map_index: int
) -> None:
    dossier, chat = evidence
    plain = vote_prompt(dossier, chat, map_index)
    assert tally_prompt(dossier, chat, map_index, "plain") == plain
    visible_evidence, plain_format = plain.rsplit("\n", 1)
    vote_fields = '"choice":"<option label>","cited":["<fact alias>"]'
    tally_fields = '"totals":{"X":<integer>,"Y":<integer>,"Z":<integer>}'
    assert plain_format == f"Return only a JSON object: {{{vote_fields}}}."
    for arm, fields in (
        ("first", f"{tally_fields},{vote_fields}"),
        ("last", f"{vote_fields},{tally_fields}"),
    ):
        shown, response_format = tally_prompt(dossier, chat, map_index, arm).rsplit(
            "\n", 1
        )
        assert shown == visible_evidence
        assert response_format == (
            "Return only a JSON object with these fields in this order: "
            f"{{{fields}}}. Use signed integers for totals."
        )


def test_plain_action_preserves_original_decode_and_exact_response(
    evidence: tuple[dict, dict],
) -> None:
    dossier, chat = evidence
    raw = ' \n{ "cited": ["K5", "K2"], "choice": "Y" } \t'
    action, readout = tally_action(raw, dossier, chat, 0, "plain")
    assert action == vote_action(raw, dossier, chat, 0)
    assert readout == {
        "view_sha256": sha256(
            vote_prompt(dossier, chat, 0).encode("utf-8")
        ).hexdigest(),
        "totals": None,
        "field_order": ["cited", "choice"],
        "response_text": raw,
    }


@pytest.mark.parametrize(
    ("arm", "order"),
    [
        ("first", ["choice", "cited", "totals"]),
        ("last", ["totals", "cited", "choice"]),
    ],
)
def test_wrong_totals_and_unrequested_field_order_do_not_change_the_vote(
    evidence: tuple[dict, dict], arm: str, order: list[str]
) -> None:
    dossier, chat = evidence
    # Visible distinct scores are X=19, Y=12, Z=10. The model reports Y as its
    # largest total but votes for Z anyway. Neither set of totals may choose.
    fields = {
        "choice": "Z",
        "cited": ["K5", "K2"],
        "totals": {"Z": 0, "Y": 200, "X": -30},
    }
    raw = " \n" + json.dumps({key: fields[key] for key in order}, indent=1) + "\t"
    action, readout = tally_action(raw, dossier, chat, 0, arm)
    assert action == vote_action('{"choice":"Z","cited":["K5","K2"]}', dossier, chat, 0)
    assert action == {"choice": "Option_C", "cited_facts": ["F_PRIV_3", "F_PRIV_1"]}
    assert readout == {
        "view_sha256": sha256(
            vote_prompt(dossier, chat, 0).encode("utf-8")
        ).hexdigest(),
        "totals": {"X": -30, "Y": 200, "Z": 0},
        "field_order": order,
        "response_text": raw,
    }


@pytest.mark.parametrize("arm", ["first", "last"])
@pytest.mark.parametrize(
    "raw",
    [
        '{"choice":"X","cited":["K2"]}',
        '{"choice":"X","cited":["K2"],"totals":{"X":0,"Y":0}}',
        '{"choice":"X","cited":["K2"],"totals":{"X":0,"Y":0,"Z":0,"W":0}}',
        '{"choice":"X","cited":["K2"],"totals":{"X":0,"Y":0,"Z":0,"X":1}}',
        '{"choice":"X","cited":["K2"],"totals":{"X":true,"Y":0,"Z":0}}',
        '{"choice":"X","cited":["K2"],"totals":{"X":0.0,"Y":0,"Z":0}}',
        '{"choice":"X","cited":["K2"],"totals":{"X":"0","Y":0,"Z":0}}',
        '{"choice":"X","cited":["K2"],"totals":{"X":NaN,"Y":0,"Z":0}}',
        '{"choice":"X","cited":["K2"],"totals":[0,0,0]}',
        '{"choice":"X","cited":["K2"],"totals":{"X":0,"Y":0,"Z":0},"extra":0}',
        '{"choice":"X","choice":"Y","cited":["K2"],"totals":{"X":0,"Y":0,"Z":0}}',
        '{"choice":"Option_A","cited":["K2"],"totals":{"X":0,"Y":0,"Z":0}}',
        '{"choice":"X","cited":["K7"],"totals":{"X":0,"Y":0,"Z":0}}',
        '{"choice":"X","cited":["K2","K2"],"totals":{"X":0,"Y":0,"Z":0}}',
    ],
)
def test_structured_vote_rejects_invalid_responses(
    evidence: tuple[dict, dict], arm: str, raw: str
) -> None:
    dossier, chat = evidence
    with pytest.raises(ValueError) as error:
        tally_action(raw, dossier, chat, 0, arm)
    assert raw not in str(error.value)


@pytest.mark.parametrize("arm", ["first", "last"])
def test_structured_vote_response_length_limit(
    evidence: tuple[dict, dict], arm: str
) -> None:
    dossier, chat = evidence
    raw = ' {"choice":"X","cited":["K2"],"totals":{"X":0,"Y":0,"Z":0}}'
    accepted = raw.ljust(8192)
    _, readout = tally_action(accepted, dossier, chat, 0, arm)
    assert readout["response_text"] == accepted
    with pytest.raises(ValueError, match="too long"):
        tally_action(raw.ljust(8193), dossier, chat, 0, arm)


def test_plain_vote_rejects_totals(evidence: tuple[dict, dict]) -> None:
    dossier, chat = evidence
    raw = '{"choice":"X","cited":["K2"],"totals":{"X":0,"Y":0,"Z":0}}'
    with pytest.raises(ValueError):
        tally_action(raw, dossier, chat, 0, "plain")


def test_tally_rejects_other_study_arms(evidence: tuple[dict, dict]) -> None:
    dossier, chat = evidence
    with pytest.raises(ValueError, match="unknown tally arm"):
        tally_prompt(dossier, chat, 0, "novelty")
    with pytest.raises(ValueError, match="unknown tally arm"):
        tally_action('{"choice":"X","cited":["K2"]}', dossier, chat, 0, "novelty")
