import json
import os
import re
import stat
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from random import Random

import pytest

from examples.seatlab.visibility import route_memory as m
from examples.seatlab.visibility import route_memory_run as runner
from examples.seatlab.visibility import route_packet, route_protocol

_FIELDS = (("latency", "reliability"), ("latency", "safety"), ("reliability", "safety"))
_REPORTS = (((71, 72), (73, 74)), ((75, 76), (77, 78)), ((79, 80), (81, 82)))
_TRUTH = ((11, 22, 33), (44, 55, 66))
_REVIEW = ' \n{"vote":"B","a":[91,92],"b":[93,94]} \n'
_DECISION = ' \n{"choice":"A"} \n'
_BAD = "unreadable reply ☃"
_ASK = {label: json.dumps({"ask": label}) for label in "XY"}
_USAGE: dict[str, str | int] = {
    "served_model": "seat-fixture",
    "input_tokens": 5,
    "cache_tokens": 2,
    "output_tokens": 3,
}

type Turn = tuple[str, str, str]
type Inputs = tuple[Path, str, Path]


def _inputs(root: Path) -> Inputs:
    root = root.resolve()
    study = root / "study"
    study.mkdir(mode=0o700)
    cases = []
    for policy in range(3):
        for rnd in range(1, 7):
            base = rnd * 12
            truth_seqs = (base + 2, base + 4, base + 6)
            if (policy, rnd) == (2, 1):
                truth_seqs = (102, 104, 106)
            cases.append(
                route_packet.Case(
                    policy,
                    rnd,
                    _REPORTS,
                    1,
                    sha256(f"ledger-{policy}".encode()).hexdigest(),
                    sha256(f"trial-{policy}".encode()).hexdigest(),
                    truth=_TRUTH,
                    report_seqs=(base + 7, base + 8, base + 9),
                    fetch_seq=base + 10,
                    decision_seq=base + 11,
                    truth_seqs=truth_seqs,
                )
            )
    raw = (
        json.dumps(
            {
                "schema": "seatlab.visibility-route-packet/v1",
                "cases": [asdict(case) for case in cases],
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode()
    packet = root / "packet.json"
    packet.write_bytes(raw)
    return packet, sha256(raw).hexdigest(), study


def _view(block: route_protocol.Block, index: int) -> route_protocol.View:
    a, b = _REPORTS[index]
    if block.swap:
        a, b = b, a
    return route_protocol.View(_FIELDS[index], a, b)


def _role(stage: str) -> str:
    return "route_review" if stage.endswith("-review") else "route_decide"


def _plan(
    block: route_protocol.Block,
    menu: m.Menu,
    bound: str,
    anonymous: str,
    invalid_review: str | None,
) -> list[Turn]:
    bound_first = (block.rotation + block.swap + block.rep) % 2 == 0
    arms = ("bound", "anonymous") if bound_first else ("anonymous", "bound")
    picks = {"bound": bound, "anonymous": anonymous}
    turns = [
        (arm, m.select_prompt(menu, arm), _ASK.get(picks[arm], _BAD)) for arm in arms
    ]
    x_step = 1 if (block.policy + block.round + block.swap + block.rep) % 2 == 0 else -1
    peers = {
        "X": _view(block, (block.rotation + x_step) % 3),
        "Y": _view(block, (block.rotation - x_step) % 3),
    }
    x_first = (block.policy + block.round + block.rotation + block.rep) % 2 == 0
    order = "XY" if x_first else "YX"
    for label in order:
        if label not in picks.values():
            continue
        peer = peers[label]
        turns.append(
            (
                f"{label.lower()}-review",
                route_protocol.review_prompt(peer),
                _BAD if label == invalid_review else _REVIEW,
            )
        )
        if label != invalid_review:
            turns.append(
                (
                    f"{label.lower()}-decide",
                    route_protocol.decision_prompt(
                        _view(block, block.rotation),
                        peer.fields,
                        route_protocol.parse_review(_REVIEW),
                        "evidence",
                    ),
                    _DECISION,
                )
            )
    return turns


def _receipt(
    block_id: str,
    digest: str,
    stage: str,
    prompt: str,
    text: str | None,
    usage: dict[str, str | int] | None = None,
) -> dict[str, object]:
    return {
        "schema": "seatlab.memory-turn/v1",
        "block": block_id,
        "stage": stage,
        "run": f"{block_id}-{stage}",
        "role": _role(stage),
        "packet_sha256": digest,
        "prompt_sha256": sha256(prompt.encode()).hexdigest(),
        "status": "callback_error" if text is None else "received",
        "text": text,
        "response_sha256": None if text is None else sha256(text.encode()).hexdigest(),
        "usage": usage,
    }


def _unexpected(_prompt: str) -> str:
    pytest.fail("unexpected callback")


def _simulate_cell(
    inputs: Inputs, block: route_protocol.Block, plan: list[Turn]
) -> None:
    packet, digest, study = inputs
    block_id = "ma-" + block.id.removeprefix("rv-")
    cell = study / block_id
    calls: list[tuple[str, str, set[str]]] = []

    def reply(role: str, prompt: str) -> str:
        _, _, text = plan[len(calls)]
        calls.append((role, prompt, {path.name for path in cell.iterdir()}))
        return text

    result = runner.play_block(
        *inputs,
        block,
        reviewer=lambda prompt: reply("route_review", prompt),
        decider=lambda prompt: reply("route_decide", prompt),
    )
    assert result == "complete"
    assert stat.S_IMODE(cell.stat().st_mode) == 0o700
    assert json.loads((cell / "claim.json").read_bytes()) == {
        "schema": "seatlab.memory-claim/v1",
        "block": block_id,
        "packet_sha256": digest,
    }
    names = {"claim.json"}
    for call, turn in zip(calls, plan, strict=True):
        role, seen_prompt, seen_names = call
        stage, prompt, text = turn
        assert (role, seen_prompt, seen_names) == (_role(stage), prompt, names)
        assert not any(
            value in prompt for value in (str(packet), str(study), block_id, digest)
        )
        path = cell / f"{stage}.json"
        names.add(path.name)
        receipt = json.loads(path.read_bytes())
        assert receipt == _receipt(block_id, digest, stage, prompt, text)
    assert {path.name for path in cell.iterdir()} == names
    for path in cell.iterdir():
        info = path.lstat()
        assert stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
        assert info.st_uid == os.geteuid() and info.st_nlink == 1
    before = {path.name: path.read_bytes() for path in cell.iterdir()}
    with pytest.raises(runner.StudyError, match="^block_already_claimed$"):
        runner.play_block(*inputs, block, reviewer=_unexpected, decider=_unexpected)
    assert {path.name: path.read_bytes() for path in cell.iterdir()} == before


def test_memory_first_attempt_simulation(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    cases = route_packet.load(*inputs[:2])
    scenarios = [
        ("X", "X", None),
        ("?", "Y", None),
        ("X", "Y", None),
        ("Y", "X", "X"),
        ("Y", "X", "Y"),
        ("?", "?", None),
    ]
    blocks = [block for block in m.blocks() if block.policy == 0 and block.round == 3]
    Random(4137).shuffle(blocks)
    for block, (bound, anonymous, bad_review) in zip(
        blocks, scenarios * 2, strict=True
    ):
        menu = m.menu(cases, block)
        assert menu is not None
        plan = _plan(block, menu, bound, anonymous, bad_review)
        for _, prompt, _ in plan[:2]:
            visible = set(map(int, re.findall(r"\d+", prompt)))
            hidden = {score for peer in menu.peers for score in (*peer.a, *peer.b)}
            hidden.update((*_TRUTH[0], *_TRUTH[1]))
            assert visible.isdisjoint(hidden)
        _simulate_cell(inputs, block, plan)


def test_memory_ambiguous_failure_simulation(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    packet, digest, study = inputs
    block = route_protocol.Block(0, 3, 0, 0, 0)
    block_id = "ma-p0-r3-s0-m0-d0"
    cases = route_packet.load(packet, digest)
    menu = m.menu(cases, block)
    assert menu is not None
    plan = _plan(block, menu, "X", "Y", None)
    calls: list[tuple[str, str, str]] = []

    def respond(prompt: str, run: str, role: str) -> runner.Answer:
        attempt = len(calls)
        calls.append((prompt, run, role))
        if attempt == 2:
            raise OSError(f"private transport diagnostic {packet}")
        return runner.Answer(plan[attempt][2], "seat-fixture", 5, 2, 3)

    with pytest.raises(runner.StudyError, match="^callback_error$") as error:
        runner._play(*inputs, block, respond)
    assert error.value.__context__ is None
    assert calls == [
        (prompt, f"{block_id}-{stage}", _role(stage)) for stage, prompt, _ in plan[:3]
    ]
    cell = study / block_id
    assert {path.name for path in cell.iterdir()} == {
        "claim.json",
        "bound.json",
        "anonymous.json",
        "y-review.json",
    }
    for index, (stage, prompt, text) in enumerate(plan[:3]):
        accepted = index < 2
        assert json.loads((cell / f"{stage}.json").read_bytes()) == _receipt(
            block_id,
            digest,
            stage,
            prompt,
            text if accepted else None,
            _USAGE if accepted else None,
        )
    with pytest.raises(runner.StudyError, match="^block_already_claimed$"):
        runner.play_block(*inputs, block, reviewer=_unexpected, decider=_unexpected)


def test_memory_refuses_unavailable_or_unsafe_cells(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    packet, digest, study = inputs
    ineligible = route_protocol.Block(2, 3, 0, 0, 0)
    result = runner.play_block(
        *inputs, ineligible, reviewer=_unexpected, decider=_unexpected
    )
    assert result == "ineligible"
    eligible = route_protocol.Block(0, 3, 0, 0, 0)
    cases = (
        (packet, "0" * 64, study, eligible, "invalid_packet"),
        (packet, digest, study, route_protocol.Block(0, 2, 0, 0, 0), "invalid_block"),
        (
            packet,
            digest,
            study,
            route_protocol.Block(0, 3, 0, 0, False),
            "invalid_block",
        ),
    )
    for path, commitment, directory, block, code in cases:
        with pytest.raises(runner.StudyError, match=f"^{code}$"):
            runner.play_block(
                path,
                commitment,
                directory,
                block,
                reviewer=_unexpected,
                decider=_unexpected,
            )
    link = study.parent / "linked-study"
    link.symlink_to(study, target_is_directory=True)
    with pytest.raises(runner.StudyError, match="^study_io_failed$"):
        runner.play_block(
            packet, digest, link, eligible, reviewer=_unexpected, decider=_unexpected
        )
    assert not list(study.iterdir())
