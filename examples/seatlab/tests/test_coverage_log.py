import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from threading import Event

import pytest

from examples.seatlab.coverage import log
from examples.seatlab.coverage.jobs import Request
from examples.seatlab.visibility.clarify_run import StudyError
from examples.seatlab.visibility.route_run import Answer

_SOURCE = "a" * 64
_PROMPT = "Private handoff note for this shipment."
_PARTIAL = '{"choices":{"A":"X"}}'
_BATCH = Request(
    f"cv-{_SOURCE[:12]}-003-batch", 3, "batch", None, _PROMPT, ("A", "B"), ("X", "Y")
)
_CHECKLIST = replace(_BATCH, key=f"cv-{_SOURCE[:12]}-003-checklist", policy="checklist")
_OWNER = replace(
    _BATCH, key=f"cv-{_SOURCE[:12]}-003-owner1", policy="owners", target="B", ids=("B",)
)


def _study(tmp_path: Path) -> Path:
    study = tmp_path.resolve() / "study"
    study.mkdir(mode=0o700)
    return study


def _reply(_prompt: str, _run: str, _role: str) -> Answer:
    return Answer('{"choices":{"A":"X","B":"Y"}}', "fixture", 12, 3, 4)


def test_workers_share_a_study_but_never_share_a_claim(tmp_path: Path) -> None:
    study = _study(tmp_path)
    started, release = Event(), Event()
    requests = (_CHECKLIST, _BATCH)
    calls: list[tuple[str, str, str]] = []

    def respond(prompt: str, run: str, role: str) -> Answer:
        calls.append((prompt, run, role))
        started.set()
        if not release.wait(timeout=10):
            raise RuntimeError("fixture timed out")
        return Answer(_PARTIAL)

    with ThreadPoolExecutor(max_workers=2) as workers:
        winner = workers.submit(log.play, study, _SOURCE, _BATCH, respond)
        try:
            assert started.wait(timeout=10)
            before = log.collect(study, _SOURCE, requests)
            assert [row["state"] for row in before] == ["unclaimed", "claimed"]
            assert [row["status"] for row in before] == ["missing", "missing"]
            assert all(row["choices"] is None for row in before)
            loser = workers.submit(log.play, study, _SOURCE, _BATCH, respond)
            with pytest.raises(StudyError, match="^block_already_claimed$"):
                loser.result(timeout=10)
            other = workers.submit(log.play, study, _SOURCE, _CHECKLIST, _reply)
            checklist = other.result(timeout=10)
        finally:
            release.set()
        batch = winner.result(timeout=10)

    assert calls == [(_PROMPT, _BATCH.key, "route_decide")]
    assert log.collect(study, _SOURCE, requests) == [checklist, batch]
    assert checklist["choices"] == {"A": "X", "B": "Y"}
    assert batch == {
        "run": _BATCH.key,
        "role": "route_decide",
        "slot": 3,
        "policy": "batch",
        "target": None,
        "state": "claimed",
        "status": "received",
        "choices": None,
        "prompt_sha256": sha256(_PROMPT.encode()).hexdigest(),
        "response_sha256": sha256(_PARTIAL.encode()).hexdigest(),
        "usage": None,
    }
    public = json.dumps([checklist, batch])
    assert _PROMPT not in public and json.dumps(_PARTIAL) not in public
    receipt = study / _BATCH.key / "response.json"
    assert receipt.stat().st_mode & 0o777 == 0o600
    assert json.loads(receipt.read_text(encoding="utf-8"))["text"] == _PARTIAL


@pytest.mark.parametrize(
    ("source", "recorded_request"),
    [
        (_SOURCE[:12] + "b" * 52, _BATCH),
        (_SOURCE, replace(_BATCH, prompt="different private prompt")),
        (_SOURCE, replace(_BATCH, ids=("C", "B"))),
        (_SOURCE, replace(_BATCH, options=("X", "Z"))),
    ],
)
def test_collect_rejects_changed_binding(
    tmp_path: Path, source: str, recorded_request: Request
) -> None:
    study = _study(tmp_path)
    log.play(study, _SOURCE, _BATCH, _reply)
    with pytest.raises(StudyError, match="^invalid_evidence$"):
        log.collect(study, source, (recorded_request,))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "missing"),
        ("status", "callback_error"),
        ("response_sha256", "b" * 64),
        ("slot", 3.0),
    ],
)
def test_collect_rejects_changed_first_response(
    tmp_path: Path, field: str, value: object
) -> None:
    study = _study(tmp_path)
    log.play(study, _SOURCE, _BATCH, _reply)
    path = study / _BATCH.key / "response.json"
    receipt = json.loads(path.read_text(encoding="utf-8"))
    receipt[field] = value
    path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(StudyError, match="^invalid_evidence$"):
        log.collect(study, _SOURCE, (_BATCH,))


@pytest.mark.parametrize(
    "bad_request",
    [
        replace(_BATCH, key=f"../{_BATCH.key}"),
        replace(_BATCH, key=f"cv-{'b' * 12}-003-batch"),
        replace(_BATCH, slot=4),
        replace(_BATCH, target="A"),
        replace(_OWNER, target="A"),
        replace(_OWNER, key=f"cv-{_SOURCE[:12]}-003-owner01"),
    ],
)
def test_bad_request_cannot_claim(tmp_path: Path, bad_request: Request) -> None:
    study = _study(tmp_path)
    with pytest.raises(StudyError, match="^invalid_request$"):
        log.play(study, _SOURCE, bad_request, _reply)
    assert list(study.iterdir()) == []
    assert list(study.parent.iterdir()) == [study]


def test_orphan_response_and_unexpected_file_fail_closed(tmp_path: Path) -> None:
    study = _study(tmp_path)
    log.play(study, _SOURCE, _BATCH, _reply)
    folder = study / _BATCH.key
    extra = folder / ".orphan.tmp"
    extra.touch(mode=0o600)
    with pytest.raises(StudyError, match="^unexpected_evidence$"):
        log.collect(study, _SOURCE, (_BATCH,))
    extra.unlink()
    (folder / "claim.json").unlink()
    with pytest.raises(StudyError, match="^invalid_evidence$"):
        log.collect(study, _SOURCE, (_BATCH,))


def test_crash_before_or_after_claim_blocks_retry(tmp_path: Path) -> None:
    study = _study(tmp_path)
    (study / _CHECKLIST.key).mkdir(mode=0o700)

    class Crash(BaseException):
        pass

    def crash(_prompt: str, _run: str, _role: str) -> Answer:
        raise Crash

    with pytest.raises(Crash):
        log.play(study, _SOURCE, _BATCH, crash)
    rows = log.collect(study, _SOURCE, (_BATCH, _CHECKLIST))
    assert [(row["state"], row["status"], row["choices"]) for row in rows] == [
        ("claimed", "missing", None),
        ("unbound", "missing", None),
    ]
    for request in (_BATCH, _CHECKLIST):
        with pytest.raises(StudyError, match="^block_already_claimed$"):
            log.play(study, _SOURCE, request, crash)


def test_interrupt_publishes_callback_error_before_reraising(tmp_path: Path) -> None:
    study = _study(tmp_path)

    def cancel(_prompt: str, _run: str, _role: str) -> Answer:
        raise KeyboardInterrupt(_PROMPT)

    with pytest.raises(KeyboardInterrupt) as caught:
        log.play(study, _SOURCE, _OWNER, cancel)
    assert not caught.value.args
    (row,) = log.collect(study, _SOURCE, (_OWNER,))
    assert row["state"] == "claimed" and row["status"] == "callback_error"
    assert row["target"] == "B"
    assert row["choices"] is row["response_sha256"] is row["usage"] is None
    assert _PROMPT not in json.dumps(row)
