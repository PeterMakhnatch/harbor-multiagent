import json
from hashlib import sha256
from importlib.resources import files
from pathlib import Path

import pytest

from examples.seatlab.handoff import journal
from examples.seatlab.handoff.packet import Block, grid, parse
from examples.seatlab.packing.source import load
from examples.seatlab.visibility.clarify_run import StudyError
from examples.seatlab.visibility.route_run import Answer


@pytest.fixture
def study(tmp_path: Path) -> Path:
    path = tmp_path.resolve() / "study"
    path.mkdir(mode=0o700)
    return path


def _blocks() -> tuple[Block, ...]:
    return grid(
        parse(files("examples.seatlab.handoff").joinpath("rota.json").read_bytes())
    )


def _record(study: Path, block: Block, text: str) -> Path:
    def respond(_prompt: str, run: str, _role: str) -> Answer:
        if run != f"{block.key}-note":
            raise RuntimeError("downstream callback failed")
        return Answer(text, "fixture/served", 4, 1, 2)

    journal.play(study, block, respond)
    return study / block.key / "note.json"


@pytest.mark.parametrize("size", [1, 96, 97, 256, 257, 8181])
def test_source_uses_first_response_at_any_original_receipt_size(
    study: Path, size: int
) -> None:
    blocks = _blocks()
    note = "x" * (size % 2) + "é" * (size // 2)
    text = json.dumps({"note": note}, ensure_ascii=False, separators=(",", ":"))
    _record(study, blocks[0], text)
    original, _ = journal.collect(study, blocks)
    frozen = load(study, blocks)
    entry, untouched = frozen.entries

    assert original["outcome"]["note_status"] == ("valid" if size <= 256 else "invalid")
    assert frozen.packet_sha256 == blocks[0].fingerprint
    assert entry.block == blocks[0]
    assert entry.status == "usable"
    assert entry.note == note
    assert entry.response_sha256 == sha256(text.encode()).hexdigest()
    assert untouched.block == blocks[1]
    assert untouched.status == "missing"
    assert untouched.note is None and untouched.response_sha256 is None
    assert load(study, blocks).sha256 == frozen.sha256


@pytest.mark.parametrize(
    "text",
    [
        '{"note":""}',
        '{"note":"first","note":"second"}',
        '{"note":"note","extra":0}',
        r'{"note":"line\nbreak"}',
        r'{"note":"\ud800"}',
        "not JSON",
    ],
)
def test_received_unusable_responses_keep_their_digest(study: Path, text: str) -> None:
    blocks = _blocks()
    _record(study, blocks[0], text)
    entry = load(study, blocks).entries[0]

    assert entry.status == "invalid"
    assert entry.note is None
    assert entry.response_sha256 == sha256(text.encode()).hexdigest()


def test_missing_writer_states_stay_on_the_register(study: Path) -> None:
    blocks = _blocks()
    block = blocks[0]
    directory = study / block.key
    unclaimed = load(study, blocks)
    directory.mkdir(mode=0o700)
    unbound = load(study, blocks)
    directory.rmdir()

    def fail(_prompt: str, _run: str, _role: str) -> Answer:
        raise RuntimeError("private transport detail")

    journal.play(study, block, fail)
    failed = load(study, blocks)
    (directory / "note.json").unlink()
    claimed = load(study, blocks)

    for frozen in (unclaimed, unbound, failed, claimed):
        assert tuple(entry.block for entry in frozen.entries) == blocks
        assert all(entry.status == "missing" for entry in frozen.entries)
        assert all(entry.note is None for entry in frozen.entries)
        assert all(entry.response_sha256 is None for entry in frozen.entries)
    assert len({unclaimed.sha256, unbound.sha256, failed.sha256, claimed.sha256}) == 4


def test_changed_receipt_is_rejected_or_changes_the_seal(study: Path) -> None:
    blocks = _blocks()
    marker = "private writer marker"
    text = json.dumps({"note": marker + "x" * 270})
    receipt = _record(study, blocks[0], text)
    before = load(study, blocks)
    assert marker not in repr(before)
    assert marker not in repr(before.entries[0])
    assert blocks[0].case.records[0].before not in repr(before.entries[0])
    assert marker not in json.dumps(journal.collect(study, blocks))

    changed = json.loads(receipt.read_bytes())
    changed["text"] = f" {text} "
    receipt.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(StudyError, match="^invalid_evidence$") as caught:
        load(study, blocks)
    assert marker not in str(caught.value)
    assert str(study) not in str(caught.value)

    changed["response_sha256"] = sha256(changed["text"].encode()).hexdigest()
    receipt.write_text(json.dumps(changed), encoding="utf-8")
    after = load(study, blocks)
    assert before.entries[0].note == after.entries[0].note
    assert before.sha256 != after.sha256

    changed["usage"]["output_tokens"] += 1
    receipt.write_text(json.dumps(changed), encoding="utf-8")
    assert load(study, blocks).sha256 != after.sha256

    changed["prompt_sha256"] = "0" * 64
    receipt.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(StudyError, match="^invalid_evidence$"):
        load(study, blocks)


def test_load_checks_the_whole_register_and_rejects_invalid_utf8(study: Path) -> None:
    blocks = _blocks()
    with pytest.raises(StudyError, match="^invalid_register$"):
        load(study, blocks[:1])

    receipt = _record(study, blocks[1], '{"note":"private second-slot marker"}')
    original = receipt.read_bytes()
    receipt.write_bytes(original.replace(b"private second-slot marker", b"\xff"))
    with pytest.raises(StudyError, match="^invalid_evidence$"):
        load(study, blocks)

    receipt.write_bytes(original)
    receipt.chmod(0o644)
    with pytest.raises(StudyError, match="^invalid_evidence$"):
        load(study, blocks)
