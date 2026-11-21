import json
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from examples.seatlab.handoff.journal import collect, play
from examples.seatlab.handoff.packet import Block, grid, parse
from examples.seatlab.visibility.clarify_run import StudyError
from examples.seatlab.visibility.route_run import Answer

_STAGES = ("note", "bound-select", "anonymous-select", "both-decide")
_REPLIES = (
    '{"note":"private first note marker"}',
    '{"read":"P"}',
    '{"read":"P"}',
    '{"choices":{"P":"B","Q":"A"}}',
)


def _blocks() -> tuple[Block, ...]:
    packet = {
        "schema": "seatlab.handoff-input/v1",
        "brief": "Read the stock records.",
        "choices": ["A", "B"],
        "cases": [
            {
                "id": "stock",
                "cluster": "north",
                "records": [
                    {
                        "id": "P",
                        "old_version": "1",
                        "new_version": "2",
                        "before": "old stock",
                        "after": "new stock",
                        "question": "Which bay for P?",
                        "old_best": "A",
                        "best": "B",
                    },
                    {
                        "id": "Q",
                        "old_version": "1",
                        "new_version": "1",
                        "before": "same stock",
                        "after": "same stock",
                        "question": "Which bay for Q?",
                        "old_best": "A",
                        "best": "A",
                    },
                ],
            }
        ],
    }
    return grid(parse(json.dumps(packet).encode()))


def _rejects(call: Callable[[], object], reason: str) -> None:
    try:
        call()
    except StudyError as error:
        assert str(error) == reason
    else:
        raise AssertionError(f"expected {reason}")


def test_first_attempt_replay_across_callback_cuts() -> None:
    with TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        for cut in range(len(_STAGES) + 1):
            study = root / str(cut)
            study.mkdir(mode=0o700)
            blocks = _blocks()
            block, other = blocks
            assert [row["state"] for row in collect(study, blocks)] == [
                "unclaimed",
                "unclaimed",
            ]
            (study / other.key).mkdir(mode=0o700)
            calls: list[str] = []

            def respond(_prompt: str, run: str, role: str) -> Answer:
                index = len(calls)
                assert run == f"{block.key}-{_STAGES[index]}"
                assert role == ("route_review" if index == 0 else "route_decide")
                calls.append(run)
                if index == cut:
                    if _STAGES[index] == "both-decide":
                        raise KeyboardInterrupt("private provider detail")
                    raise RuntimeError("private provider detail")
                return Answer(_REPLIES[index], "fixture/served", 4, 1, 2)

            played = None
            try:
                played = play(study, block, respond)
            except KeyboardInterrupt as interrupted:
                assert cut == len(_STAGES) - 1
                assert interrupted.args == ()
            report, unbound = collect(study, blocks)
            assert report["state"] == "claimed"
            assert unbound["state"] == "unbound"
            assert unbound["turns"] == []
            assert unbound["outcome"]["note_status"] == "missing"
            assert len(calls) == min(cut + 1, len(_STAGES))
            assert [turn["run"] for turn in report["turns"]] == calls
            _rejects(lambda: play(study, block, respond), "block_already_claimed")
            assert len(calls) == len(report["turns"])
            assert "private first note marker" not in json.dumps(report)
            if played is not None:
                assert played == report["outcome"]
            if cut < len(_STAGES):
                failed = report["turns"][-1]
                assert failed["status"] == "callback_error"
                assert failed["response_sha256"] is None
                assert failed["usage"] is None
            else:
                assert len(report["turns"]) == 4
                arms = report["outcome"]["arms"]
                assert arms["bound"] == arms["anonymous"]
                assert arms["bound"]["status"] == "complete"
                assert arms["bound"]["decision_stage"] == "both-decide"
                assert arms["bound"]["choices"] == {"P": "B", "Q": "A"}


def test_replay_rejects_broken_history_and_impossible_shared_final() -> None:
    with TemporaryDirectory() as temporary:
        study = Path(temporary).resolve()
        blocks = _blocks()
        block = blocks[0]
        answers = iter(_REPLIES)
        play(study, block, lambda _prompt, _run, _role: Answer(next(answers)))
        directory = study / block.key
        note = directory / "note.json"
        original = note.read_bytes()
        forged = json.loads(original)
        text = '{"note":"a different but valid note"}'
        forged["text"] = text
        note.write_text(json.dumps(forged), encoding="utf-8")
        _rejects(lambda: collect(study, blocks), "invalid_evidence")
        forged["response_sha256"] = sha256(text.encode()).hexdigest()
        note.write_text(json.dumps(forged), encoding="utf-8")
        _rejects(lambda: collect(study, blocks), "invalid_evidence")
        note.write_bytes(original)

        shared = directory / "both-decide.json"
        invented = json.loads(shared.read_bytes())
        unreachable = directory / "bound-decide.json"
        shared.rename(unreachable)
        invented["stage"] = "bound-decide"
        invented["run"] = f"{block.key}-bound-decide"
        unreachable.write_text(json.dumps(invented), encoding="utf-8")
        _rejects(lambda: collect(study, blocks), "unexpected_evidence")
