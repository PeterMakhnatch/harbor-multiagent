import importlib
import json
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

import pytest
import test_visibility_boxes_packet as fixture
from test_visibility_intent_packet import joint_panel, panel  # noqa: F401

from harbor.models.task.task import Task
from harbor.publisher.packager import Packager

with pytest.MonkeyPatch.context() as patch:
    patch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    source = importlib.import_module("visibility.ownership_source")


def _read_json(path):
    return json.loads(path.read_bytes())


def _read_events(directory):
    return [
        json.loads(line)
        for line in (directory / fixture._EVENT_PATH).read_bytes().splitlines()
    ]


def _write_events(directory, rows):
    for seq, row in enumerate(rows, 1):
        row["seq"] = seq
    rows[-1]["response"]["events"] = len(rows)
    fixture._events_file(directory / fixture._EVENT_PATH, rows)


def _expected_prefix_sha256(expected):
    encoded = json.dumps(
        expected, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


@pytest.fixture
def replay_inputs(joint_panel, monkeypatch):  # noqa: F811
    root, selected, unselected = joint_panel
    packet = root.parent / "intentions.json"
    raw = source.intent_packet.export(root)
    packet.write_bytes(raw)
    worlds = source.intent_packet.load(packet, sha256(raw).hexdigest())
    monkeypatch.setattr(source.intent_packet, "load", lambda: worlds)

    directory = selected[1]
    current = _read_events(directory)
    prior_opens, later = current[:5], current[5:]
    prefix = [
        fixture._event(0, 4.1, None, "msg", None, {"error": "unauth"}, status=401),
        fixture._event(0, 4.2, "p1", "open", {"box": 0}, {"error": "bad"}, status=400),
    ]
    messages = []

    def post(at, text):
        seat = fixture._SEATS[len(messages) % 3]
        response = {"id": len(messages) + 1, "from": seat, "text": text}
        messages.append(response)
        return fixture._event(0, at, seat, "msg", {"text": text}, response)

    prefix.append(post(4.5, " private café "))
    for index, row in enumerate(prior_opens):
        prefix.append(row)
        prefix.append(post(row["t"] - fixture._START.timestamp(), f"private {index}"))
    for index in range(3):
        prefix.append(post(16, f"private at anchor time {index}"))
    prefix.append(
        fixture._event(
            0, 16, "p1", "msgs", None, {"messages": messages.copy()}, method="GET"
        )
    )
    expected = [
        {key: row[key] for key in ("seat", "kind", "request", "response")}
        for row in prefix
        if row["ok"] and row["kind"] in {"open", "msg"}
    ]
    _write_events(directory, prefix + [later[0], post(16, "future post"), *later[1:]])

    tasks = root.parent / "tasks"
    task = tasks / "boxes-i01"
    (task / "tests").mkdir(parents=True)
    (task / "environment").mkdir()
    (task / "task.toml").write_text('schema_version = "1.4"\n')
    (task / "instruction.md").write_text("Find flags.\n")
    (task / "environment/Dockerfile").write_text("FROM alpine:3.20\n")
    (task / "tests/test.sh").write_text("#!/bin/sh\n")
    with pytest.warns(DeprecationWarning, match="Task.checksum"):
        checksum = Task(task).checksum
    digest, _ = Packager.compute_content_hash(task)
    result_path, lock_path = directory / "result.json", directory / "lock.json"
    result, lock = _read_json(result_path), _read_json(lock_path)
    result["task_checksum"] = checksum
    lock["task"]["digest"] = f"sha256:{digest}"
    fixture._json_file(result_path, result)
    fixture._json_file(lock_path, lock)
    monkeypatch.setitem(
        source._REGISTERED_IDENTITIES,
        1,
        (_expected_prefix_sha256(expected), checksum, lock["task"]["digest"]),
    )
    return root, tasks, directory, unselected[1], expected


def test_prepare_keeps_exact_pre_anchor_order_and_distinct_flags(replay_inputs):
    root, tasks, directory, _, expected = replay_inputs
    cut = source.prepare(root, tasks, 1)
    assert [asdict(action) for action in cut.actions] == expected
    assert cut.prefix_sha256 == _expected_prefix_sha256(expected)
    assert cut.baseline_flags == 2  # The two opens of box 1 count once.
    assert cut.instance == 1
    assert cut.task == tasks / "boxes-i01"
    assert cut.task_checksum == _read_json(directory / "result.json")["task_checksum"]
    assert cut.task_digest == _read_json(directory / "lock.json")["task"]["digest"]
    assert "private café" not in repr(cut.actions)
    assert str(cut.task) not in repr(cut)


def test_prepare_never_backfills_a_missing_selected_history(replay_inputs):
    root, tasks, directory, alternative, _ = replay_inputs
    _write_events(alternative, _read_events(directory))
    (directory / fixture._EVENT_PATH).unlink()
    with pytest.raises(ValueError, match="retained ownership events"):
        source.prepare(root, tasks, 1)
    with pytest.raises(ValueError, match="not a registered ownership world"):
        source.prepare(root, tasks, 3)


@pytest.mark.parametrize("mutation", ("owner", "order", "flag"))
def test_prepare_rejects_valid_service_history_that_differs_from_world(
    replay_inputs, mutation
):
    root, tasks, directory, _, _ = replay_inputs
    rows = _read_events(directory)
    opens = [row for row in rows if row["ok"] and row["kind"] == "open"]
    if mutation == "flag":
        for row in opens:
            if row["response"]["room"] == 1:
                row["response"]["flag"] = False
    else:
        # Exchange boxes between two seats, or exchange a seat's own opens.
        first, second = (
            (opens[2], opens[3]) if mutation == "owner" else (opens[1], opens[2])
        )
        first["request"], second["request"] = second["request"], first["request"]
        for key in ("box", "room", "flag"):
            first["response"][key], second["response"][key] = (
                second["response"][key],
                first["response"][key],
            )
    _write_events(directory, rows)
    with pytest.raises(ValueError, match="open history differs from the pinned world"):
        source.prepare(root, tasks, 1)


@pytest.mark.parametrize("mutation", ("sender", "id_order", "message_text", "readback"))
def test_prepare_requires_exact_authenticated_message_sequence(replay_inputs, mutation):
    root, tasks, directory, _, _ = replay_inputs
    rows = _read_events(directory)
    posts = [row for row in rows if row["ok"] and row["kind"] == "msg"]
    readback = next(row for row in rows if row["kind"] == "msgs")["response"][
        "messages"
    ]
    if mutation == "sender":
        posts[0]["response"]["from"] = "p3"
    elif mutation == "id_order":
        posts[0]["response"]["id"], posts[1]["response"]["id"] = 2, 1
        readback[0]["id"], readback[1]["id"] = 2, 1
    elif mutation == "message_text":
        posts[0]["request"]["text"] = "changed post"
    else:
        readback.reverse()
    _write_events(directory, rows)
    with pytest.raises(ValueError, match="retained ownership events"):
        source.prepare(root, tasks, 1)


def test_prepare_rejects_a_consistent_rewrite_of_a_prefix_message(replay_inputs):
    root, tasks, directory, _, _ = replay_inputs
    rows = _read_events(directory)
    post = next(row for row in rows if row["ok"] and row["kind"] == "msg")
    message_id = post["response"]["id"]
    new_text = "different but internally consistent"
    post["request"]["text"] = post["response"]["text"] = new_text
    for row in rows:
        if row["ok"] and row["kind"] == "msgs":
            for message in row["response"]["messages"]:
                if message["id"] == message_id:
                    message["text"] = new_text
    _write_events(directory, rows)
    with pytest.raises(ValueError, match="prefix differs from the registered digest"):
        source.prepare(root, tasks, 1)


def test_prepare_requires_both_retained_identities(replay_inputs):
    root, tasks, directory, _, _ = replay_inputs
    task = tasks / "boxes-i01"
    cache = task / "environment/__pycache__"
    cache.mkdir()
    (cache / "stale.pyc").write_bytes(b"transient")
    with pytest.raises(
        ValueError, match="legacy checksum.*package digest still matches"
    ):
        source.prepare(root, tasks, 1)
    (cache / "stale.pyc").unlink()
    cache.rmdir()

    lock_path = directory / "lock.json"
    lock = _read_json(lock_path)
    lock["task"]["digest"] = "sha256:" + "0" * 64
    fixture._json_file(lock_path, lock)
    with pytest.raises(
        ValueError, match="package digest differs from the retained lock"
    ):
        source.prepare(root, tasks, 1)

    (task / "tests/test.sh").write_text("#!/bin/sh\n# changed content\n")
    with pytest.raises(ValueError, match="legacy checksum and package digest differ"):
        source.prepare(root, tasks, 1)


def test_prepare_rejects_a_matching_replacement_task_and_archive(replay_inputs):
    root, tasks, directory, _, _ = replay_inputs
    task = tasks / "boxes-i01"
    (task / "tests/test.sh").write_text("#!/bin/sh\n# replacement\n")
    result_path, lock_path = directory / "result.json", directory / "lock.json"
    result, lock = _read_json(result_path), _read_json(lock_path)
    with pytest.warns(DeprecationWarning, match="Task.checksum"):
        result["task_checksum"] = Task(task).checksum
    digest, _ = Packager.compute_content_hash(task)
    lock["task"]["digest"] = f"sha256:{digest}"
    fixture._json_file(result_path, result)
    fixture._json_file(lock_path, lock)
    with pytest.raises(
        ValueError, match="legacy checksum differs from the registered task"
    ):
        source.prepare(root, tasks, 1)


@pytest.mark.parametrize("target", ("archive", "task"))
def test_prepare_rechecks_mutable_inputs(replay_inputs, monkeypatch, target):
    root, tasks, directory, _, _ = replay_inputs
    check = source.bp._Snapshot.check
    if target == "archive":
        path = directory / fixture._EVENT_PATH
        message = "retained source changed"
    else:
        path = tasks / "boxes-i01/tests/test.sh"
        message = "native ownership task source changed"

    def change_during_check(snapshot):
        path.write_bytes(path.read_bytes() + b"\n")
        check(snapshot)

    monkeypatch.setattr(source.bp._Snapshot, "check", change_during_check)
    with pytest.raises(ValueError, match=message):
        source.prepare(root, tasks, 1)
