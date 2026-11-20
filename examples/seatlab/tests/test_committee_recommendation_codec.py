import json

import pytest

from examples.seatlab.committee import contract
from examples.seatlab.committee import recommendation as rec
from examples.seatlab.committee.schedule import ROLES

_MAP = 0
_CHAIR = ROLES[2]
_FACT_ORDER = (ROLES[2], ROLES[0], ROLES[3], ROLES[1])
_PROPOSAL_ORDER = (ROLES[3], ROLES[1], ROLES[2], ROLES[0])
_CHOICES = {
    ROLES[0]: ("Option_B", ["F_PRIV_1", "F_SHARED_1"]),
    ROLES[1]: ("Option_A", ["F_PRIV_2", "F_SHARED_4"]),
    ROLES[2]: ("Option_C", ["F_SHARED_5", "F_PRIV_3"]),
    ROLES[3]: ("Option_B", ["F_SHARED_3", "F_PRIV_4"]),
}
_RECOMMENDATION = {"choice": "Option_A", "cited_facts": ["F_PRIV_3", "F_PRIV_2"]}


def _dossier(role: str) -> dict:
    shared = ["Option_B"] * 3 + ["Option_A", "Option_C"]
    i = ROLES.index(role) + 1

    def fact(fact_id: str, option: str, points: int) -> dict:
        return {
            "id": fact_id,
            "kind": "private-native-kind",
            "option": option,
            "points": points,
            "text": "native prose must not reach the model",
        }

    return {
        "seat": role,
        "options": ["Option_A", "Option_B", "Option_C"],
        "rubric": "native rubric must not reach the model",
        "shared_facts": [
            fact(f"F_SHARED_{n}", option, 10) for n, option in enumerate(shared, 1)
        ],
        "private_facts": [
            fact(f"F_PRIV_{i}", "Option_B" if i % 2 else "Option_A", -9 if i % 2 else 9)
        ],
    }


def _snapshot(messages: list[dict]) -> dict:
    return {
        "messages": messages,
        "unique_speakers": sorted({m["seat"] for m in messages}),
    }


@pytest.fixture
def messages() -> list[dict]:
    _, aliases = contract._mapping(_MAP)
    shares = dict(
        zip(ROLES, ("F_SHARED_1", "F_PRIV_2", "F_PRIV_3", "F_SHARED_1"), strict=True)
    )
    rows = []
    for i, role in enumerate(_FACT_ORDER):
        action = contract.share_action(
            json.dumps({"fact": aliases[shares[role]]}), _dossier(role), _MAP
        )
        rows.append({"seat": role, "seq": 3 * i + 8, **action})
    for i, role in enumerate(_PROPOSAL_ORDER):
        choice, cited = _CHOICES[role]
        action = rec.post("proposal", {"choice": choice, "cited_facts": cited}, _MAP)
        rows.append({"seat": role, "seq": 4 * i + 21, **action})
    rows.append(
        {"seat": _CHAIR, "seq": 44, **rec.post("recommend", _RECOMMENDATION, _MAP)}
    )
    return rows


def _section(prompt: str, name: str):
    prefix = f"{name}: "
    (line,) = (line for line in prompt.splitlines() if line.startswith(prefix))
    return json.loads(line.removeprefix(prefix))


def _prompt(phase: str, messages: list[dict], role: str, arm: str) -> str:
    view = rec.board(_snapshot(messages), _MAP, _CHAIR)
    return rec.turn_prompt(phase, _dossier(role), view, _MAP, role, _CHAIR, arm)


def test_fast_peer_posts_do_not_change_the_first_ballot_view(
    messages: list[dict],
) -> None:
    partial = rec.board(_snapshot(messages[:2]), _MAP, _CHAIR)
    assert partial.facts == _snapshot(messages[:2])
    assert partial.proposals == {}
    assert partial.recommendation is None
    fact_view = rec.board(_snapshot(messages[:4]), _MAP, _CHAIR)
    fast_view = rec.board(_snapshot(messages[:5]), _MAP, _CHAIR)
    assert fast_view.facts == fact_view.facts == _snapshot(messages[:4])
    assert [row["seq"] for row in fast_view.facts["messages"]] == [8, 11, 14, 17]
    assert list(fast_view.proposals) == [_PROPOSAL_ORDER[0]]
    assert fast_view.recommendation is None
    prompt = contract.vote_prompt(_dossier(ROLES[0]), fast_view.facts, _MAP)
    assert prompt == contract.vote_prompt(_dossier(ROLES[0]), fact_view.facts, _MAP)
    assert "Proposals:" not in prompt


def test_complete_views_keep_authors_and_ignore_proposal_arrival_order(
    messages: list[dict],
) -> None:
    chair_prompt = _prompt("recommend", messages[:-1], _CHAIR, "shown")
    assert chair_prompt == _prompt("recommend", messages[:-1], _CHAIR, "masked")
    assert "Recommendation:" not in chair_prompt
    assert "Make one recommendation" in chair_prompt
    expected = [
        {"seat": "S1", "choice": "Y", "cited": ["K2", "K6"]},
        {"seat": "S2", "choice": "X", "cited": ["K3", "K8"]},
        {"seat": "S3", "choice": "Z", "cited": ["K1", "K5"]},
        {"seat": "S4", "choice": "Y", "cited": ["K9", "K7"]},
    ]
    assert _section(chair_prompt, "Proposals") == expected
    assert _section(chair_prompt, "Your first ballot") == {
        "choice": "Z",
        "cited": ["K1", "K5"],
    }

    plain = contract.vote_prompt(_dossier(_CHAIR), _snapshot(messages[:4]), _MAP)
    assert _section(chair_prompt, "Dossier") == _section(plain, "Dossier")
    assert _section(chair_prompt, "Submissions") == _section(plain, "Submissions")
    reordered = messages[:4] + [
        {**row, "seq": seq}
        for row, seq in zip(reversed(messages[4:8]), (21, 25, 29, 33), strict=True)
    ]
    assert _prompt("recommend", reordered, _CHAIR, "shown") == chair_prompt
    assert (
        _section(chair_prompt, "Submissions").count(
            {"fact": "K6", "option": "Y", "points": 10, "provenance": "shared"}
        )
        == 2
    )


def test_final_prompt_masks_only_the_actual_recommendation_for_nonchairs(
    messages: list[dict],
) -> None:
    actual = {"choice": "X", "cited": ["K5", "K3"]}
    full = rec.board(_snapshot(messages), _MAP, _CHAIR)
    assert full.recommendation == actual
    assert list(full.proposals) == list(_PROPOSAL_ORDER)
    alternative = {"choice": "Option_B", "cited_facts": ["F_PRIV_3"]}
    changed = messages[:-1] + [
        {**messages[-1], **rec.post("recommend", alternative, _MAP)}
    ]
    for role in ROLES:
        shown = _prompt("vote", messages, role, "shown")
        masked = _prompt("vote", messages, role, "masked")
        assert _section(shown, "Recommendation") == actual
        assert _section(shown, "Your first ballot") == full.proposals[role]
        assert _section(masked, "Recommendation") == (
            actual if role == _CHAIR else None
        )
        assert (shown == masked) == (role == _CHAIR)
        if role != _CHAIR:
            assert masked == _prompt("vote", changed, role, "masked")
            revealed = 'Recommendation: {"choice":"X","cited":["K5","K3"]}'
            assert shown.replace(revealed, "Recommendation: null") == masked
        assert shown != _prompt("vote", changed, role, "shown")
        for prompt in (shown, masked):
            for hidden in (
                *ROLES,
                "Option_",
                "F_PRIV_",
                "F_SHARED_",
                "native prose",
                "native rubric",
                "private-native-kind",
                "masked",
                "shown",
                "seq",
            ):
                assert hidden not in prompt


@pytest.mark.parametrize("arm", ["shown", "masked"])
def test_prompt_requires_the_authenticated_complete_phase(
    messages: list[dict], arm: str
) -> None:
    for phase, source, role in (
        ("recommend", messages[:7], _CHAIR),
        ("recommend", messages[:8], ROLES[0]),
        ("recommend", messages, _CHAIR),
        ("vote", messages[:8], ROLES[0]),
        ("proposal", messages, ROLES[0]),
    ):
        with pytest.raises(ValueError):
            _prompt(phase, source, role, arm)
    view = rec.board(_snapshot(messages), _MAP, _CHAIR)
    with pytest.raises(ValueError):
        rec.turn_prompt("vote", _dossier(ROLES[1]), view, _MAP, ROLES[0], _CHAIR, arm)
    with pytest.raises(ValueError):
        rec.turn_prompt("vote", _dossier(_CHAIR), view, 1, _CHAIR, _CHAIR, arm)
    with pytest.raises(ValueError):
        rec.turn_prompt("vote", _dossier(_CHAIR), view, _MAP, _CHAIR, ROLES[0], arm)


def test_rejects_wrong_authors_and_source_window_order(messages: list[dict]) -> None:
    for source in (
        [messages[1], messages[0]],
        [messages[0], {**messages[1], "seq": messages[0]["seq"]}],
        [messages[0], {**messages[1], "seq": True}],
        [{**messages[0], "seat": "world"}],
        [{**messages[0], "seat": ROLES[0]}],
        [{**messages[0], "author": "S3"}],
        messages[:2] + [{**messages[1], "seq": 13}],
        messages[:2]
        + [{**messages[2], "text": messages[2]["text"].replace("10", "11")}],
        messages[:3] + messages[4:5],  # Proposal before all four facts.
        messages[:5]
        + [{**messages[4], "seq": 24}],  # Second proposal from the same seat.
        messages[:7] + messages[8:],  # Recommendation before all four proposals.
        messages[:8] + [{**messages[8], "seat": ROLES[0]}],
        messages + [{**messages[8], "seq": 45}],
    ):
        with pytest.raises(ValueError):
            rec.board(_snapshot(source), _MAP, _CHAIR)
    for speakers in (list(reversed(ROLES)), [*ROLES, ROLES[0]], list(ROLES[:3])):
        with pytest.raises(ValueError):
            rec.board({"messages": messages, "unique_speakers": speakers}, _MAP, _CHAIR)


@pytest.mark.parametrize(
    "text",
    [
        'PROPOSAL {"choice":"Y","cited":["K7"],"seat":"S4"}',
        'PROPOSAL {"choice":"Y","cited":["K7"],"cited":["K6"]}',
        'PROPOSAL {"cited":["K7"],"choice":"Y"}',
        'PROPOSAL  {"choice":"Y","cited":["K7"]}',
        'PROPOSAL {"choice":"Y","cited":["K7"]}\n',
        'PROPOSAL {"choice":"Y","cited":["\\u004b7"]}',
        'PROPOSAL {"choice":"Y","cited":["K7","K7"]}',
        'PROPOSAL {"choice":"Y","cited":["K2"]}',  # An unshared fact from another seat.
        'PROPOSAL {"choice":"Y","cited":["F_PRIV_4"]}',
        'PROPOSAL {"choice":"Option_B","cited":["K7"]}',
        'PROPOSAL {"choice":"Y","cited":["K10"]}',
        'PROPOSAL {"choice":"Y","cited":[]}',
        'PROPOSAL {"choice":"Y","cited":[true]}',
        'PROPOSAL {"choice":"Y","cited":[NaN]}',
        'VOTE {"choice":"Y","cited":["K7"]}',
        "PROPOSAL " + "[" * 256,
    ],
)
def test_rejects_noncanonical_and_smuggled_proposals(
    messages: list[dict], text: str
) -> None:
    with pytest.raises(ValueError):
        rec.board(
            _snapshot(messages[:4] + [{**messages[4], "text": text}]), _MAP, _CHAIR
        )


@pytest.mark.parametrize(
    "text",
    [
        '{"id":"F_PRIV_3", "option":"Option_B","points":-9}',
        '{"option":"Option_B","points":-9,"id":"F_PRIV_3"}',
        '{"id":"F_PRIV_3","option":"Option_B","points":-9,"text":"leak"}',
        '{"id":"F_PRIV_3","option":"Option_B","points":-9.0}',
        '{"id":"F_PRIV_3","option":"Option_B","points":true}',
        '{"id":"F_PRIV_3","option":"Option_T","points":-9}',
    ],
)
def test_rejects_invalid_native_fact_text(messages: list[dict], text: str) -> None:
    with pytest.raises(ValueError):
        rec.board(_snapshot([{**messages[0], "text": text}]), _MAP, _CHAIR)


def test_post_and_native_vote_grammar(messages: list[dict]) -> None:
    assert rec.blind_vote(_RECOMMENDATION, _MAP) == {
        "choice": "X",
        "cited": ["K5", "K3"],
    }
    assert rec.post("recommend", _RECOMMENDATION, _MAP) == {
        "text": 'RECOMMEND {"choice":"X","cited":["K5","K3"]}'
    }
    assert rec.post("proposal", _RECOMMENDATION, _MAP) == {
        "text": 'PROPOSAL {"choice":"X","cited":["K5","K3"]}'
    }
    for invalid in (
        {**_RECOMMENDATION, "seat": _CHAIR},
        {**_RECOMMENDATION, "choice": "X"},
        {**_RECOMMENDATION, "cited_facts": ["F_PRIV_3", "F_PRIV_3"]},
        {**_RECOMMENDATION, "cited_facts": ["F_PRIV_5"]},
        {**_RECOMMENDATION, "cited_facts": [True]},
        {**_RECOMMENDATION, "cited_facts": []},
        {**_RECOMMENDATION, "cited_facts": "F_PRIV_3"},
        {**_RECOMMENDATION, "cited_facts": ["F_PRIV_3"] * 10},
    ):
        with pytest.raises(ValueError):
            rec.blind_vote(invalid, _MAP)
        with pytest.raises(ValueError):
            rec.post("recommend", invalid, _MAP)
    with pytest.raises(ValueError):
        rec.post("vote", _RECOMMENDATION, _MAP)
    full = rec.board(_snapshot(messages), _MAP, _CHAIR)
    text = '{"choice":"X","cited":["K5","K3"]}'
    assert (
        contract.vote_action(text, _dossier(_CHAIR), full.facts, _MAP)
        == _RECOMMENDATION
    )
    for text in (
        '{"choice":"X","cited":["K7"]}',  # The proposal does not share F_PRIV_4 as a fact.
        '{"choice":"X","cited":["K5"],"totals":{}}',
        'RECOMMEND {"choice":"X","cited":["K5"]}',
    ):
        with pytest.raises(ValueError):
            contract.vote_action(text, _dossier(_CHAIR), full.facts, _MAP)
