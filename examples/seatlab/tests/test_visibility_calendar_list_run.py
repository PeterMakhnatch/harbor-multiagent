from hashlib import sha256
from threading import Event, Thread

import pytest

from examples.seatlab.visibility import calendar_list_protocol as protocol
from examples.seatlab.visibility import calendar_list_run as run


def test_concurrent_workers_cannot_both_pay_for_one_cell(tmp_path):
    cell = run._plan()[0]
    started, release = Event(), Event()
    seen, statuses = [], []

    def receiver(prompt):
        seen.append(prompt)
        started.set()
        if not release.wait(5):
            raise TimeoutError
        return '{ "slot":11 }'

    def first():
        statuses.append(run.collect(tmp_path, cell.id, receiver))

    worker = Thread(target=first)
    worker.start()
    try:
        assert started.wait(5)
        with pytest.raises(run.CaptureError, match="attempt_already_exists"):
            run.collect(tmp_path, cell.id, receiver)
    finally:
        release.set()
        worker.join(7)
    assert statuses == ["complete"]
    assert seen == [protocol.prompt(cell.case, cell.condition)]
    with run._root(tmp_path) as root, run._cell_dir(root, cell.id) as fd:
        receipt = run._read(fd, "response.json")
    assert receipt["text"] == '{ "slot":11 }'
    assert receipt["response_sha256"] == sha256(receipt["text"].encode()).hexdigest()


def test_invalid_response_is_retained_but_missing_transport_is_not_a_zero(tmp_path):
    first, second = run._plan()[:2]
    assert run.collect(tmp_path, first.id, lambda _: '{"slot":35}') == "model_invalid"
    with run._root(tmp_path) as root, run._cell_dir(root, first.id) as fd:
        assert run._read(fd, "response.json")["text"] == '{"slot":35}'

    def failed(_):
        raise RuntimeError("private provider diagnostic")

    with pytest.raises(run.CaptureError, match="^receiver_unavailable$"):
        run.collect(tmp_path, second.id, failed)
    assert not (tmp_path / second.id / "response.json").exists()
    with pytest.raises(run.CaptureError, match="attempt_already_exists"):
        run.collect(tmp_path, second.id, lambda _: '{"slot":0}')


def test_capture_fails_before_callback_in_shared_or_symlinked_storage(tmp_path):
    cell = run._plan()[0]
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o755)
    link = tmp_path / "linked"
    link.symlink_to(tmp_path, target_is_directory=True)
    seen = []
    for path in (link, shared):
        with pytest.raises((run.CaptureError, OSError)):
            run.collect(
                path, cell.id, lambda prompt: seen.append(prompt) or '{"slot":3}'
            )
    assert not seen
