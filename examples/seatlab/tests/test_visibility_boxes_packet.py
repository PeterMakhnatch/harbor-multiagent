import importlib
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest

with pytest.MonkeyPatch.context() as patch:
    patch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    packet = importlib.import_module("visibility.boxes_packet")

_START = datetime(2026, 10, 1, tzinfo=UTC)
_JOB_ID = str(UUID(int=1))
_SEATS = ("p1", "p2", "p3")
_FALLBACK = {"name": "oracle", "resume_trajectory": False}
_PARTICIPANTS = [
    {
        "seat": seat,
        "name": "terminus-2",
        "model_name": "deepseek/deepseek-flash",
        "resume_trajectory": False,
        "load_trajectory": None,
    }
    for seat in _SEATS
]
_EVAL = "terminus-2__deepseek-flash__adhoc"
_EVENT_PATH = Path("verifier/events.jsonl")


def _canonical(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _sha(raw):
    return sha256(raw).hexdigest()


def _json_file(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(value))


def _stamp(seconds):
    return (_START + timedelta(seconds=seconds)).isoformat()


def _interval(start, end):
    return {"started_at": _stamp(start), "finished_at": _stamp(end)}


def _trial(root, instance, attempt):
    case = f"boxes-i{instance:02}"
    name = f"{case}__run{attempt:04}"
    trial_id = str(UUID(int=10 * instance + attempt))
    directory = root / name
    config = {
        "participants": _PARTICIPANTS,
        "agent": _FALLBACK,
        "trial_name": name,
        "job_id": _JOB_ID,
        "task": {"path": f"/not-a-source/{case}"},
        "source_trial": None,
        "install_only": False,
        "verifier": {"disable": False},
        "extra_instructions": [],
        "extra_instruction_paths": [],
    }
    _json_file(
        directory / "result.json",
        {
            **_interval(0, 31),
            "id": trial_id,
            "trial_name": name,
            "config": config,
            "task_name": f"muster/{case}",
            "task_id": config["task"],
            "task_checksum": "a" * 64,
            "source": None,
            "reward_valid": True,
            "exception_info": None,
            "agent_execution": _interval(2, 25),
            "verifier": _interval(27, 30),
            "seat_results": [
                {
                    "seat": seat,
                    "under_test": True,
                    "exception_info": None,
                    "agent_execution": _interval(3, 24),
                }
                for seat in _SEATS
            ],
            "verifier_result": {"rewards": {"reward": 0.125}},
        },
    )
    rank = _sha(f"seatlab-box-target-v1:{trial_id.lower()}".encode())
    return directory, rank


def _event(seq, seconds, seat, kind, request, response, *, method="POST", status=200):
    return {
        "seq": seq,
        "t": _START.timestamp() + seconds,
        "seat": seat,
        "kind": kind,
        "method": method,
        "request": request,
        "response": response,
        "status": status,
        "ok": status == 200,
    }


def _events_file(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\n".join(map(_canonical, events)) + b"\n")


def _agent_step(i, seconds, observations):
    return {
        "step_id": i,
        "source": "agent",
        "timestamp": _stamp(seconds),
        "observation": {"results": [{"content": observations}]},
    }


def _screen(response):
    return f"Current Terminal Screen:\n{json.dumps(response)}agent@private:~$ "


def _trajectory(directory, instance):
    seat = _SEATS[(instance - 1) % 3]
    return directory / "seats" / seat / "agent/trajectory.json"


def _history(directory, instance, *, earlier):
    own = _SEATS[(instance - 1) % 3]
    peer1, peer2 = (seat for seat in _SEATS if seat != own)
    result = json.loads((directory / "result.json").read_bytes())
    config = result["config"]
    task = config["task"]["path"]
    _json_file(
        directory / "config.json", {k: v for k, v in config.items() if k != "agent"}
    )
    _json_file(
        directory / "lock.json",
        {
            "task": {
                "name": Path(task).name,
                "path": task,
                "type": "local",
                "digest": "sha256:" + "b" * 64,
            },
            "participants": _PARTICIPANTS,
        },
    )
    events = [
        _event(
            1,
            4,
            None,
            "rules",
            None,
            {"error": "private unauthenticated"},
            method="GET",
            status=401,
        ),
        _event(
            2,
            4.5,
            own,
            "msg",
            {"text": "private text"},
            {"from": own, "id": 1, "text": "private text"},
        ),
    ]
    actions = [
        (5, peer1, 1),
        (6, own, 1),
        (7, peer1, 11),
        (8, own, 2),
        (10, own, 1),  # Not the cutoff: there is still only 1 peer-only box.
        (13, peer2, 12),
        (16, own, 4),  # Cut before this open.
        (18, peer1, 42),
        (20, own, 5),
    ]
    budgets = dict.fromkeys(_SEATS, 9)
    own_replies = []
    for seconds, seat, box in actions:
        room = (box - 1) // 10 + 1
        budgets[seat] -= 1
        reply = {
            "box": box,
            "room": room,
            "flag": room == 1,
            "budget_left": budgets[seat],
        }
        events.append(
            _event(len(events) + 1, seconds, seat, "open", {"box": box}, reply)
        )
        if seat == own:
            own_replies.append(reply)
    events.append(
        _event(
            len(events) + 1,
            28,
            None,
            "admin.close",
            {},
            {"closed": True, "events": len(events) + 1},
        )
    )
    _events_file(directory / _EVENT_PATH, events)

    instruction = ["private initial text"] * 65
    for index, line in zip((52, 58, 60, 62, 64), packet._RULES, strict=True):
        instruction[index] = line
    steps = [
        {
            "step_id": 1,
            "source": "user",
            "timestamp": _stamp(4),
            "message": "\n".join(instruction),
        },
        _agent_step(2, 6.5, _screen(own_replies[0])),
        _agent_step(3, 8.5, _screen(own_replies[1])),
        _agent_step(4, 10.5, _screen(own_replies[2]) if earlier else "no reply yet"),
        _agent_step(5, 16.5, _screen(own_replies[2]) + "\n" + _screen(own_replies[3])),
        _agent_step(6, 21, _screen(own_replies[4])),
    ]
    _json_file(
        _trajectory(directory, instance),
        {"schema_version": "ATIF-v1.8", "steps": steps},
    )


@pytest.fixture
def panel(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "team-3"
    ranks = []
    selected = {}
    unselected = {}
    for instance in range(1, 11):
        trials = [_trial(root, instance, attempt) for attempt in range(3)]
        ranked = sorted(trials, key=lambda trial: trial[1])
        selected[instance], unselected[instance] = ranked[0][0], ranked[1][0]
        ranks.extend((path.name, rank) for path, rank in trials)
    monkeypatch.setattr(packet, "_ROSTER_SHA256", _sha(_canonical(sorted(ranks))))
    _json_file(
        root / "config.json",
        {
            "job_name": "team-3",
            "n_attempts": 3,
            "retry": {"max_retries": 0},
            "source_jobs": [],
            "datasets": [],
            "agents": [_FALLBACK],
            "participants": _PARTICIPANTS,
            "install_only": False,
            "verifier": {"disable": False},
            "extra_instructions": [],
            "extra_instruction_paths": [],
            "tasks": [{"path": f"/not-a-source/boxes-i{i:02}"} for i in range(1, 11)],
        },
    )
    _json_file(
        root / "result.json",
        {
            "id": _JOB_ID,
            "finished_at": _stamp(33),
            "n_total_trials": 30,
            "stats": {
                "n_completed_trials": 30,
                "n_retries": 0,
                "n_errored_trials": 0,
                "n_running_trials": 0,
                "n_pending_trials": 0,
                "n_cancelled_trials": 0,
                "evals": {
                    _EVAL: {
                        "n_trials": 30,
                        "n_errors": 0,
                        "reward_stats": {
                            "reward": {"0.125": [name for name, _ in reversed(ranks)]}
                        },
                    }
                },
            },
        },
    )
    _history(selected[1], 1, earlier=True)
    _history(unselected[1], 1, earlier=True)
    _history(selected[2], 2, earlier=False)
    return root, selected, unselected


def test_export_preserves_the_cut_and_never_reads_unselected_histories(
    panel, monkeypatch, tmp_path
):
    root, selected, _ = panel
    read = packet._read

    def only_registered(path, limit):
        assert path.is_relative_to(root)
        if path != root / "config.json" and path.name != "result.json":
            assert (
                path.is_relative_to(selected[1])
                or path.is_relative_to(selected[2])
                or (path.parent in selected.values() and path.name == "config.json")
            )
        return read(path, limit)

    with monkeypatch.context() as patch:
        patch.setattr(packet, "_read", only_registered)
        raw = packet.export(root)
        assert packet.export(root) == raw
    path = tmp_path.resolve() / "packet.json"
    path.write_bytes(raw)
    cases = packet.load(path, _sha(raw))
    own = ((1, 1, True), (2, 1, True), (1, 1, True))
    assert list(cases) == list(range(1, 11))
    assert cases[1] == packet.Case(1, own, (1, 11, 12), 6, True)
    assert cases[2] == packet.Case(2, own, (1, 11, 12), 6, False)
    assert all(cases[instance] is None for instance in range(3, 11))
    exported = json.loads(raw)
    assert exported["cases"][2] == {"instance": 3, "status": "missing_source"}
    assert set(exported["cases"][0]) == {
        "instance",
        "status",
        "own",
        "peers",
        "budget_left",
        "earlier_observation",
    }
    for private in (
        b"private",
        b"reward",
        b"not-a-source",
        b"run000",
        _JOB_ID.encode(),
    ):
        assert private not in raw


def test_missing_verifier_log_does_not_replace_selected_trial_or_use_artifact_copy(
    panel,
):
    root, selected, _ = panel
    world = selected[1] / _EVENT_PATH
    copy = selected[1] / "artifacts/logs/world/events.jsonl"
    copy.parent.mkdir(parents=True)
    world.rename(copy)
    assert json.loads(packet.export(root))["cases"][0] == {
        "instance": 1,
        "status": "missing_source",
    }


def test_rejects_unregistered_uuid_even_if_manifest_counts_are_unchanged(panel):
    root, _, unselected = panel
    path = unselected[5] / "result.json"
    result = json.loads(path.read_bytes())
    result["id"] = str(UUID(int=400))
    _json_file(path, result)
    with pytest.raises(ValueError, match="registered team-3 roster"):
        packet.export(root)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("sequence", "invalid_events"),
        ("bool_box", "invalid_events"),
        ("duplicate_field", "invalid_events"),
        ("unknown_actor", "invalid_events"),
        ("fake_sender", "invalid_events"),
        ("room", "invalid_events"),
        ("missing_close", "invalid_events"),
        ("close_count", "invalid_events"),
        ("too_late", "invalid_timing"),
        ("late_flag", "invalid_timing"),
        ("no_cut", "no_eligible_cut"),
        ("symlink", "invalid_events"),
    ],
)
def test_bad_selected_history_stays_typed_without_exporting_action(
    panel, mutation, reason
):
    root, selected, _ = panel
    path = selected[1] / _EVENT_PATH
    events = [json.loads(line) for line in path.read_bytes().splitlines()]
    if mutation == "sequence":
        events[0]["seq"] = True
    elif mutation == "bool_box":
        events[2]["request"]["box"] = True
    elif mutation == "unknown_actor":
        events[2]["seat"] = "unknown"
    elif mutation == "fake_sender":
        events[1]["response"]["from"] = "p3"
    elif mutation == "room":
        events[2]["response"]["room"] = 2
    elif mutation == "missing_close":
        events.pop()
    elif mutation == "close_count":
        events[-1]["response"]["events"] += 1
    elif mutation == "too_late":
        events[-1]["t"] += 3
    elif mutation == "late_flag":
        events[2]["late"] = True
    elif mutation == "no_cut":
        # All accepted peer opens are in the own set. Preserve service timing,
        # per-seat budgets, and successful verification.
        for event in events:
            if event["kind"] == "open" and event["seat"] != "p1":
                event["request"]["box"] = 1
                event["response"].update(box=1, room=1, flag=True)
    if mutation == "symlink":
        target = path.with_name("replacement.jsonl")
        path.rename(target)
        path.symlink_to(target)
    else:
        _events_file(path, events)
        if mutation == "duplicate_field":
            raw = path.read_bytes().replace(b'"seq":1,', b'"seq":1,"seq":1,', 1)
            path.write_bytes(raw)
    assert json.loads(packet.export(root))["cases"][0] == {
        "instance": 1,
        "status": reason,
    }


def test_observation_never_uses_a_plan_command_or_future_result(panel):
    root, selected, _ = panel
    path = _trajectory(selected[1], 1)
    trajectory = json.loads(path.read_bytes())
    steps = trajectory["steps"]
    own = {"box": 1, "room": 1, "flag": True, "budget_left": 6}
    quoted = json.dumps(own)
    # The apparent prior match in step 2 predates this service reply. Step 4
    # mentions it in a plan, tool arguments, and terminal command, not a result.
    steps[1]["observation"]["results"].append({"content": _screen(own)})
    steps[3]["message"] = _screen(own)
    steps[3]["tool_calls"] = [{"arguments": {"keystrokes": _screen(own)}}]
    steps[3]["observation"]["results"] = [
        {"content": f"agent@private:~$ echo '{quoted}'"}
    ]
    _json_file(path, trajectory)
    first = json.loads(packet.export(root))["cases"][0]
    assert first["status"] == "eligible"
    assert first["earlier_observation"] is False


def test_rejects_changes_to_exact_initial_rules(panel):
    root, selected, _ = panel
    path = _trajectory(selected[1], 1)
    trajectory = json.loads(path.read_bytes())
    trajectory["steps"][0]["message"] = trajectory["steps"][0]["message"].replace(
        "different flag boxes", "all flag boxes"
    )
    _json_file(path, trajectory)
    assert json.loads(packet.export(root))["cases"][0] == {
        "instance": 1,
        "status": "invalid_instruction",
    }


def test_rejects_source_mutation_during_export(panel, monkeypatch):
    root, selected, _ = panel
    target = selected[1] / _EVENT_PATH
    read = packet._read

    def change_after_read(path, limit):
        raw = read(path, limit)
        if path == target:
            path.write_bytes(raw + b"\n")
        return raw

    monkeypatch.setattr(packet, "_read", change_after_read)
    with pytest.raises(ValueError, match="source changed"):
        packet.export(root)


@pytest.fixture
def public_file(tmp_path):
    path = tmp_path.resolve() / "packet.json"
    cases = [
        {
            "instance": 1,
            "status": "eligible",
            "own": [[1, 1, True], [2, 1, True], [2, 1, True]],
            "peers": [1, 11, 12],
            "budget_left": 6,
            "earlier_observation": False,
        },
        *({"instance": i, "status": "no_eligible_cut"} for i in range(2, 11)),
    ]
    _json_file(path, {"schema": "seatlab.box-receiver.packet/v1", "cases": cases})
    return path


def test_load_requires_external_digest_and_regular_path(public_file, tmp_path):
    digest = _sha(public_file.read_bytes())
    with pytest.raises(ValueError, match="SHA-256 does not match"):
        packet.load(public_file, "0" * 64)
    with pytest.raises(ValueError, match="expected a lowercase SHA-256"):
        packet.load(public_file, "")
    link = tmp_path.resolve() / "packet-link.json"
    link.symlink_to(public_file)
    with pytest.raises(ValueError, match="bounded regular file"):
        packet.load(link, digest)
    directory = tmp_path.resolve() / "public"
    directory.mkdir()
    moved = public_file.rename(directory / "packet.json")
    parent_link = tmp_path.resolve() / "directory-link"
    parent_link.symlink_to(directory, target_is_directory=True)
    with pytest.raises(ValueError, match="bounded regular file"):
        packet.load(parent_link / moved.name, digest)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (b'"budget_left":6', b'"budget_left":true'),
        (b'"budget_left":6', b'"budget_left":7'),
        (b'"budget_left":6', b'"budget_left":1e999'),
        (b'"budget_left":6', b'"budget_left":NaN'),
        (b'"earlier_observation":false', b'"earlier_observation":0'),
        (b'"instance":1,', b'"instance":true,'),
        (b'"instance":2,', b'"instance":1,'),
        (b"[[1,1,true]", b"[[1,true,true]"),
        (b"[[1,1,true]", b"[[1,2,true]"),
        (b"[1,11,12]", b"[1,1,12]"),
        (b"[1,11,12]", b"[1,12,11]"),
        (b"[1,11,12]", b"[1,11,61]"),
        (b"[1,11,12]", b"[1,2,11]"),
        (b'"status":"eligible"', b'"status":"eligible","status":"eligible"'),
        (b'"status":"eligible"', b'"status":"eligible","uuid":"private"'),
        (b'"status":"no_eligible_cut"', b'"status":"unregistered"'),
        (b'"schema":', b'"schema": '),
    ],
)
def test_load_rejects_noncanonical_or_out_of_domain_data_with_matching_digest(
    public_file, old, new
):
    raw = public_file.read_bytes()
    mutation = raw.replace(old, new, 1)
    assert mutation != raw
    public_file.write_bytes(mutation)
    with pytest.raises(ValueError):
        packet.load(public_file, _sha(mutation))
