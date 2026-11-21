import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from threading import Event

import pytest

from examples.seatlab.handoff.packet import Case, Packet, Record, grid
from examples.seatlab.packing import log
from examples.seatlab.packing.source import Entry, Frozen
from examples.seatlab.visibility.clarify_run import StudyError
from examples.seatlab.visibility.route_run import Answer

_PRIVATE_NOTE = "Private inventory note for the morning shift."
_PRIVATE_REPLY = "Private reply fragment; not a decision."


def _source(note: str = _PRIVATE_NOTE) -> Frozen:
    case = Case(
        "morning",
        "warehouse",
        (
            Record(
                "crate", "1", "1", "blue", "blue", "Choose a crate bay.", "blue", "blue"
            ),
            Record(
                "desk",
                "1",
                "1",
                "green",
                "green",
                "Choose a desk bay.",
                "green",
                "green",
            ),
        ),
    )
    packet = Packet("Assign bays.", ("blue", "green"), (case,), "a" * 64)
    first, second = grid(packet)
    reply = json.dumps({"note": note}).encode("utf-8")
    return Frozen(
        packet.sha256,
        "b" * 64,
        (
            Entry(first, "usable", sha256(reply).hexdigest(), note),
            Entry(second, "missing", None, None),
        ),
    )


def _study(tmp_path: Path) -> Path:
    study = tmp_path.resolve() / "study"
    study.mkdir(mode=0o700)
    return study


def _reply(_prompt: str, _run: str, _role: str) -> Answer:
    return Answer(_PRIVATE_REPLY, "fixture", 12, 3, 4)


def test_concurrent_first_claim_and_public_replay(tmp_path: Path) -> None:
    study = _study(tmp_path)
    source = _source()
    entry = source.entries[0]
    name = log.key(entry, source.sha256)
    started, release = Event(), Event()
    calls: list[tuple[str, str]] = []

    def respond(prompt: str, run: str, role: str) -> Answer:
        assert _PRIVATE_NOTE in prompt
        calls.append((run, role))
        started.set()
        if not release.wait(timeout=10):
            raise RuntimeError("fixture timed out")
        return _reply(prompt, run, role)

    with pytest.raises(StudyError, match="^entry_not_registered$"):
        log.play(study, source, replace(entry, note="different note"), respond)
    assert not (study / name).exists()

    with ThreadPoolExecutor(max_workers=2) as workers:
        winner = workers.submit(log.play, study, source, entry, respond)
        try:
            assert started.wait(timeout=10)
            loser = workers.submit(log.play, study, source, entry, respond)
            with pytest.raises(StudyError, match="^block_already_claimed$"):
                loser.result(timeout=10)
        finally:
            release.set()
        outcome = winner.result(timeout=10)

    rows = log.collect(study, source)
    row = rows[0]
    assert calls == [(f"{name}-both-decide", "route_decide")]
    assert [item["state"] for item in rows] == ["claimed", "unclaimed"]
    assert row["fingerprint"] == source.sha256
    assert row["outcome"] == outcome
    assert outcome["arms"]["prefix"]["status"] == "invalid_decide"
    assert len(row["turns"]) == 1
    turn = row["turns"][0]
    assert set(turn) == {
        "run",
        "role",
        "status",
        "prompt_sha256",
        "response_sha256",
        "usage",
    }
    assert turn["usage"] == {
        "served_model": "fixture",
        "input_tokens": 12,
        "cache_tokens": 3,
        "output_tokens": 4,
    }
    public = json.dumps(rows)
    assert _PRIVATE_NOTE not in public and _PRIVATE_REPLY not in public
    receipt = study / name / "both-decide.json"
    assert receipt.stat().st_mode & 0o777 == 0o600
    assert json.loads(receipt.read_text(encoding="utf-8"))["text"] == _PRIVATE_REPLY


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("source_sha256", "b" * 12 + "c" * 52),
        ("packet_sha256", "a" * 12 + "c" * 52),
        ("prompt_sha256", "d" * 64),
        ("response_sha256", "e" * 64),
        ("role", "route_review"),
        ("sequence", False),
        (
            "usage",
            {
                "served_model": "fixture",
                "input_tokens": 12.0,
                "cache_tokens": 3,
                "output_tokens": 4,
            },
        ),
        ("unknown", "private extra field"),
    ],
)
def test_replay_rejects_tampered_receipt(
    tmp_path: Path, field: str, replacement: object
) -> None:
    study = _study(tmp_path)
    source = _source()
    entry = source.entries[0]
    log.play(study, source, entry, _reply)
    path = study / log.key(entry, source.sha256) / "both-decide.json"
    receipt = json.loads(path.read_text(encoding="utf-8"))
    receipt[field] = replacement
    path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(StudyError, match="^invalid_evidence$"):
        log.collect(study, source)


def test_replay_rejects_a_gap_and_a_symlink(tmp_path: Path) -> None:
    study = _study(tmp_path)
    source = _source(_PRIVATE_NOTE * 4)
    entry = source.entries[0]
    log.play(study, source, entry, _reply)
    block = study / log.key(entry, source.sha256)
    rewrite = block / "rewrite.json"
    rewrite.rename(tmp_path / "rewrite.json")
    assert (block / "prefix-decide.json").is_file()
    with pytest.raises(StudyError, match="^unexpected_evidence$"):
        log.collect(study, source)
    rewrite.symlink_to(tmp_path / "rewrite.json")
    with pytest.raises(StudyError, match="^invalid_evidence$"):
        log.collect(study, source)


def test_interrupted_claim_is_not_retried(tmp_path: Path) -> None:
    study = _study(tmp_path)
    source = _source()
    first, second = source.entries
    (study / log.key(second, source.sha256)).mkdir(mode=0o700)

    def cancel(_prompt: str, _run: str, _role: str) -> Answer:
        raise KeyboardInterrupt(_PRIVATE_REPLY)

    with pytest.raises(KeyboardInterrupt) as caught:
        log.play(study, source, first, cancel)
    assert not caught.value.args
    rows = log.collect(study, source)
    assert [row["state"] for row in rows] == ["claimed", "unbound"]
    assert [turn["status"] for turn in rows[0]["turns"]] == ["callback_error"]
    assert rows[1]["turns"] == []
    assert _PRIVATE_REPLY not in json.dumps(rows)
    for entry in source.entries:
        with pytest.raises(StudyError, match="^block_already_claimed$"):
            log.play(study, source, entry, _reply)
