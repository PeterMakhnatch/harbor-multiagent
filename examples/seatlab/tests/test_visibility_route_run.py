import json
import os
import stat
from hashlib import sha256
from types import SimpleNamespace

import pytest

from examples.seatlab.visibility import route_protocol as protocol
from examples.seatlab.visibility import route_run as runner

_DIGEST = "e" * 64
_BLOCK = protocol.Block(0, 1, 0, 0, 0)
_USAGE = {
    "served_model": "fixture-model",
    "input_tokens": 12,
    "cache_tokens": 3,
    "output_tokens": 4,
}


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    study = root / "study"
    study.mkdir(mode=0o700)
    packet = root / "packet.json"
    case = SimpleNamespace(
        policy=0,
        round=1,
        reports=(((10, 20), (30, 40)), ((10, 50), (30, 60)), ((20, 50), (40, 60))),
        best=1,
    )

    def load(path, digest):
        assert (path, digest) == (packet, _DIGEST)
        return (case,)

    monkeypatch.setattr(runner.route_packet, "load", load)
    return (packet, _DIGEST, study), case


def _review(vote):
    # The wrong numbers must reach the decision maker unchanged.
    return f'\n {{"vote":"{vote}","a":[97,98],"b":[95,96]}} \n'


def _json(path):
    return json.loads(path.read_bytes())


def _receipt(block, lens, stage, prompt, text, usage=None):
    return {
        "schema": "seatlab.routed-turn/v1",
        "block": block.id,
        "lens": lens,
        "stage": stage,
        "run": protocol.run_id(block, lens, stage),
        "role": "route_review" if stage == "review" else "route_decide",
        "packet_sha256": _DIGEST,
        "prompt_sha256": sha256(prompt.encode()).hexdigest(),
        "status": "callback_error" if text is None else "received",
        "text": text,
        "response_sha256": None if text is None else sha256(text.encode()).hexdigest(),
        "usage": usage,
    }


def _unexpected(_prompt):
    pytest.fail("unexpected callback")


def test_prompt_only_callbacks_fork_each_actual_reply_and_keep_private_receipts(inputs):
    (packet, digest, study), case = inputs
    # Different replies on equal inputs prove that repetitions make fresh calls.
    votes = iter(("A", "B", "A", "B"))
    malformed_decision = "  unreadable reply ☃\n"
    duplicate_reviews = []

    for block in (_BLOCK, protocol.Block(0, 1, 0, 0, 1)):
        directory = study / block.id
        claim = {
            "schema": "seatlab.routed-claim/v1",
            "block": block.id,
            "packet_sha256": digest,
        }
        events = []

        def reviewer(prompt):
            assert _json(directory / "claim.json") == claim
            text = _review(next(votes))
            events.append(("review", prompt, text))
            return text

        def decider(prompt):
            events.append(("decide", prompt, malformed_decision))
            return malformed_decision

        assert (
            runner.play_block(
                packet, digest, study, block, reviewer=reviewer, decider=decider
            )
            == "complete"
        )
        own = protocol.view(case, block, "duplicate")
        expected_names = {"claim.json"}
        for index, lens in enumerate(protocol.lenses(block)):
            peer = protocol.view(case, block, lens)
            prompt = protocol.review_prompt(peer)
            text = _review("AB"[index])
            assert events[3 * index] == ("review", prompt, text)
            if lens == "duplicate":
                duplicate_reviews.append((prompt, text))
            name = f"{lens}-review.json"
            expected_names.add(name)
            assert _json(directory / name) == _receipt(
                block, lens, "review", prompt, text
            )
            review = protocol.parse_review(text)
            for offset, channel in enumerate(protocol.channels(block, lens), start=1):
                decision = protocol.decision_prompt(own, peer.fields, review, channel)
                assert events[3 * index + offset] == (
                    "decide",
                    decision,
                    malformed_decision,
                )
                name = f"{lens}-{channel}.json"
                expected_names.add(name)
                assert _json(directory / name) == _receipt(
                    block, lens, channel, decision, malformed_decision
                )
        assert len(events) == 6
        assert all(
            block.id not in prompt and str(packet) not in prompt
            for _, prompt, _ in events
        )
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert {path.name for path in directory.iterdir()} == expected_names
        for path in directory.iterdir():
            info = path.lstat()
            assert stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
            assert info.st_uid == os.geteuid() and info.st_nlink == 1
        before = {path.name: path.read_bytes() for path in directory.iterdir()}
        with pytest.raises(runner.StudyError, match="^block_already_claimed$"):
            runner.play_block(
                packet, digest, study, block, reviewer=_unexpected, decider=_unexpected
            )
        assert {path.name: path.read_bytes() for path in directory.iterdir()} == before

    assert duplicate_reviews[0][0] == duplicate_reviews[1][0]
    assert duplicate_reviews[0][1] != duplicate_reviews[1][1]


def test_malformed_review_suppresses_its_children_only(inputs):
    args, _ = inputs
    raw = ' {"vote":"A","a":[10,20]} \n'
    first, second = protocol.lenses(_BLOCK)
    stages = []

    def reviewer(_prompt):
        stages.append("review")
        return raw if len(stages) == 1 else _review("B")

    def decider(_prompt):
        stages.append("decide")
        return '{"choice":"B"}'

    assert (
        runner.play_block(*args, _BLOCK, reviewer=reviewer, decider=decider)
        == "complete"
    )
    directory = args[2] / _BLOCK.id
    assert stages == ["review", "review", "decide", "decide"]
    assert {path.name for path in directory.iterdir()} == {
        "claim.json",
        f"{first}-review.json",
        f"{second}-review.json",
        f"{second}-vote.json",
        f"{second}-evidence.json",
    }
    first_review = _json(directory / f"{first}-review.json")
    assert first_review["status"] == "received"
    assert first_review["text"] == raw


def test_transport_failure_keeps_review_and_stops_before_another_turn(inputs):
    args, _ = inputs
    lens = protocol.lenses(_BLOCK)[0]
    channel = protocol.channels(_BLOCK, lens)[0]
    directory = args[2] / _BLOCK.id
    calls = []

    def respond(prompt, run, role):
        calls.append((prompt, run, role))
        if len(calls) == 1:
            return runner.Answer(_review("A"), "fixture-model", 12, 3, 4)
        assert _json(directory / f"{lens}-review.json")["text"] == _review("A")
        raise OSError(f"private transport diagnostic {args[0]}")

    with pytest.raises(runner.StudyError, match="^callback_error$") as error:
        runner._play(*args, _BLOCK, respond)
    assert error.value.__context__ is None
    assert [(run, role) for _, run, role in calls] == [
        (protocol.run_id(_BLOCK, lens, "review"), "route_review"),
        (protocol.run_id(_BLOCK, lens, channel), "route_decide"),
    ]
    review = f"{lens}-review.json"
    failure = f"{lens}-{channel}.json"
    assert {path.name for path in directory.iterdir()} == {
        "claim.json",
        review,
        failure,
    }
    assert _json(directory / review) == _receipt(
        _BLOCK, lens, "review", calls[0][0], _review("A"), _USAGE
    )
    assert _json(directory / failure) == _receipt(
        _BLOCK, lens, channel, calls[1][0], None
    )


def test_interrupt_after_review_leaves_a_hole_and_disallows_retry(inputs):
    args, _ = inputs
    lens = protocol.lenses(_BLOCK)[0]

    def interrupted(_prompt):
        raise KeyboardInterrupt("private interruption detail")

    with pytest.raises(KeyboardInterrupt, match="^$"):
        runner.play_block(
            *args, _BLOCK, reviewer=lambda _: _review("B"), decider=interrupted
        )
    directory = args[2] / _BLOCK.id
    assert {path.name for path in directory.iterdir()} == {
        "claim.json",
        f"{lens}-review.json",
    }
    assert _json(directory / f"{lens}-review.json")["text"] == _review("B")
    with pytest.raises(runner.StudyError, match="^block_already_claimed$"):
        runner.play_block(*args, _BLOCK, reviewer=_unexpected, decider=_unexpected)


@pytest.mark.parametrize(
    "answer",
    [
        runner.Answer("é" * 4097),
        runner.Answer("\ud800"),
        runner.Answer("unused", served_model="fixture-model"),
        runner.Answer("unused", "fixture-model", 2, 3, 1),
        runner.Answer("unused", "fixture-model", True, 0, 1),
    ],
    ids=[
        "utf8-limit",
        "bad-utf8",
        "partial-usage",
        "cache-exceeds-input",
        "bool-token",
    ],
)
def test_invalid_callback_contract_is_a_redacted_error(inputs, answer):
    args, case = inputs
    lens = protocol.lenses(_BLOCK)[0]
    with pytest.raises(runner.StudyError, match="^callback_error$"):
        runner._play(*args, _BLOCK, lambda *_: answer)
    directory = args[2] / _BLOCK.id
    review = f"{lens}-review.json"
    assert {path.name for path in directory.iterdir()} == {"claim.json", review}
    prompt = protocol.review_prompt(protocol.view(case, _BLOCK, lens))
    assert _json(directory / review) == _receipt(_BLOCK, lens, "review", prompt, None)


def test_ineligible_block_makes_no_claim_and_no_calls(inputs):
    args, case = inputs
    case.reports = case.best = None
    assert (
        runner.play_block(*args, _BLOCK, reviewer=_unexpected, decider=_unexpected)
        == "ineligible"
    )
    assert not list(args[2].iterdir())


def test_study_and_claim_paths_reject_links_and_unsafe_permissions(inputs):
    (packet, digest, study), _ = inputs
    linked_study = study.parent / "study-link"
    linked_study.symlink_to(study, target_is_directory=True)
    with pytest.raises(runner.StudyError, match="^study_io_failed$"):
        runner.play_block(
            packet,
            digest,
            linked_study,
            _BLOCK,
            reviewer=_unexpected,
            decider=_unexpected,
        )
    study.chmod(0o755)
    with pytest.raises(runner.StudyError, match="^unsafe_study_path$"):
        runner.play_block(
            packet, digest, study, _BLOCK, reviewer=_unexpected, decider=_unexpected
        )
    assert not list(study.iterdir())
    study.chmod(0o700)
    claim_path = study / _BLOCK.id
    claim_path.symlink_to(study.parent, target_is_directory=True)
    with pytest.raises(runner.StudyError, match="^block_already_claimed$"):
        runner.play_block(
            packet, digest, study, _BLOCK, reviewer=_unexpected, decider=_unexpected
        )
    assert claim_path.is_symlink()
    assert not (study.parent / "claim.json").exists()
