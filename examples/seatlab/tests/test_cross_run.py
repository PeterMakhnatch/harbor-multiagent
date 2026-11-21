import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from threading import Event
from typing import Any, NoReturn

import pytest

from examples.seatlab.decision_cross import inputs, run
from examples.seatlab.turn_fork import packet as fork_packet
from examples.seatlab.turn_fork import protocol
from examples.seatlab.visibility import deepseek, route_cli, seat_command, spend
from examples.seatlab.visibility.clarify_run import StudyError

_DECISION = ' {"choices":{"Q1":"A","Q2":"A","Q3":"A","Q4":"A","Q5":"A","Q6":"A"}}\n'
_PRIVATE_REPLY = "private dispatch reply and diagnostic"
_TEST_KEY = "never-forward-this-test-key"
_OPENED = ("Q1", "Q3")
_BALANCE = Decimal("2.00")
_WAIT = 10
_COMMAND = """
import json
import os
import sys

path, response, mode = sys.argv[1:]
frame = json.loads(sys.stdin.readline())
blocked = sorted({'DEEPSEEK_API_KEY', 'SEATLAB_SPEND_DIR'} & os.environ.keys())
with open(path, 'a', encoding='utf-8') as calls:
    print(json.dumps({'frame': frame, 'blocked': blocked}), file=calls)
if mode == 'broken':
    print(response, file=sys.stderr)
    print(response)
else:
    print(json.dumps({'text': response}))
"""


def _text_digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _digest(document: object) -> str:
    return _text_digest(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    )


@dataclass(frozen=True)
class Scenario:
    packet: dict[str, Any]
    board: dict[str, Any]
    digest: str
    prompt: str


@pytest.fixture
def scenario() -> Scenario:
    source = {
        "source_id": "s6501-r0",
        "seed": 6501,
        "repetition": 0,
        "trial_id": "12345678-1234-1234-1234-123456789abc",
        "world_sha256": "2" * 64,
        "upstream": {
            "inventory": {"prompt_sha256": "3" * 64, "response_sha256": "4" * 64},
            "brief": {"prompt_sha256": "5" * 64, "response_sha256": "6" * 64},
        },
        "card": "ABACDB",
        "cases": [
            {
                "id": index,
                "utilities": {"A": 4, "B": 1, "C": 2, "D": 3},
                "reports": [
                    {"revision": 1, "available": ["B", "D"]},
                    {"revision": 2, "available": ["A", "C"]},
                ],
                "after": ["A", "C"],
            }
            for index in range(6)
        ],
    }
    packet = {
        "schema": fork_packet.SCHEMA,
        "source_report_sha256": "1" * 64,
        "sources": [source],
    }
    prompt = protocol.decide_prompt(source, _OPENED)
    prompt_sha256 = _text_digest(prompt)
    obs_id = f"s6501-r0-{prompt_sha256[:12]}"
    selections = [
        {
            "source_id": f"s{seed}-r{rep}",
            "sender": sender,
            "arm": arm,
            "obs_id": None,
            "sender_response_sha256": None,
        }
        for seed in fork_packet.SEEDS
        for rep in fork_packet.REPETITIONS
        for sender in ("direct", "commands")
        for arm in ("aligned", "repeat")
    ]
    selections[0].update(
        obs_id=obs_id, sender_response_sha256=_text_digest('{"open":["Q1","Q3"]}')
    )
    board = {
        "schema": "seatlab.decision-cross-board/v1",
        "packet_sha256": _digest(packet),
        "direct_report_sha256": "7" * 64,
        "commands_report_sha256": "8" * 64,
        "observations": [
            {
                "source_id": "s6501-r0",
                "obs_id": obs_id,
                "prompt_sha256": prompt_sha256,
                "opened": list(_OPENED),
            }
        ],
        "selections": selections,
    }
    return Scenario(packet, board, _digest(board), prompt)


def _direct_env(monkeypatch: pytest.MonkeyPatch, ledger: Path) -> None:
    monkeypatch.setattr(spend, "_HOST_LEDGER", None)
    monkeypatch.setattr(deepseek, "_KEY", None)
    monkeypatch.setattr(deepseek, "_BOUND", False)
    monkeypatch.setenv("SEATLAB_SPEND_DIR", str(ledger))
    monkeypatch.setenv("DEEPSEEK_API_KEY", _TEST_KEY)


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path.resolve() / "ledger"
    root.mkdir(0o700)
    spend.initialize(root, _BALANCE, Decimal("0.20"))
    _direct_env(monkeypatch, root)
    return root


@pytest.fixture
def commitment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: Scenario
) -> Path:
    path = tmp_path.resolve() / "decision-cross.sha256"
    path.write_text(scenario.digest + "\n", encoding="ascii")
    monkeypatch.setattr(run, "_COMMITMENT", path)
    return path


@pytest.fixture
def no_direct(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def forbid(*_args: object) -> NoReturn:
        calls.append("direct")
        raise AssertionError("unexpected direct admission or turn")

    monkeypatch.setattr(route_cli, "_admit", forbid)
    monkeypatch.setattr(route_cli, "_direct", forbid)
    return calls


def _row(
    scenario: Scenario,
    index: int,
    state: str = "unclaimed",
    *,
    turn: dict[str, Any] | None = None,
) -> dict[str, Any]:
    draw = inputs.draws(scenario.board)[index]
    return {
        **draw,
        "run": inputs.run_id(scenario.digest, draw["obs_id"], draw["draw"]),
        "state": state,
        "turn": turn,
        "outcome": (
            protocol.grade(scenario.packet["sources"][0], _OPENED, _DECISION)
            if state == "complete"
            else None
        ),
    }


def _report(
    scenario: Scenario, transport: str, *rows: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema": "seatlab.decision-cross-report/v1",
        "board_sha256": scenario.digest,
        "transport": transport,
        "rows": list(rows),
    }


def _received(
    scenario: Scenario, text: str, usage: dict[str, str | int] | None = None
) -> dict[str, Any]:
    return {
        "status": "received",
        "prompt_sha256": _text_digest(scenario.prompt),
        "response_sha256": _text_digest(text),
        "usage": usage,
    }


def _run(scenario: Scenario, study: Path, indices: range) -> list[dict[str, Any]]:
    return run.run_cells(
        scenario.packet, scenario.board, scenario.digest, study, indices
    )


def _pending(scenario: Scenario, study: Path) -> list[int]:
    return run.pending(scenario.packet, scenario.board, scenario.digest, study)


def _public(scenario: Scenario, study: Path) -> dict[str, Any]:
    return run.report(scenario.packet, scenario.board, scenario.digest, study)


def _command_config(
    root: Path, text: str, *, env: tuple[str, ...] = (), broken: bool = False
) -> tuple[Path, Path]:
    config, calls = root / "dispatch.json", root / "dispatch-calls.jsonl"
    argv = [
        str(Path(sys.executable).resolve()),
        "-c",
        _COMMAND,
        str(calls),
        text,
        "broken" if broken else "valid",
    ]
    config.write_text(json.dumps({"dispatch": {"argv": argv, "env": list(env)}}))
    return config, calls


def _command_calls(scenario: Scenario, calls: Path) -> None:
    assert [json.loads(line) for line in calls.read_text().splitlines()] == [
        {
            "frame": {"schema": "seatlab.seat-turn/v1", "prompt": scenario.prompt},
            "blocked": [],
        }
    ]


@pytest.mark.usefixtures("commitment")
def test_direct_rejects_unsealed_or_changed_inputs_before_admission(
    tmp_path: Path, scenario: Scenario, ledger: Path, no_direct: list[str]
) -> None:
    root = tmp_path.resolve()
    study = root / "study"
    with pytest.raises(StudyError):
        _run(scenario, study, range(2))
    run.seal(study, scenario.board, scenario.digest, "direct", None, ledger)
    changed_board = deepcopy(scenario.board)
    changed_board["direct_report_sha256"] = "a" * 64
    changed_digest = _digest(changed_board)
    for digest in (scenario.digest, changed_digest):
        with pytest.raises(StudyError):
            run.run_cells(scenario.packet, changed_board, digest, study, range(2))
    with pytest.raises(StudyError):
        run.seal(root / "other", changed_board, changed_digest, "direct", None, ledger)
    changed_packet = deepcopy(scenario.packet)
    changed_packet["sources"][0]["card"] = "DDDDDD"
    with pytest.raises(StudyError):
        run.run_cells(changed_packet, scenario.board, scenario.digest, study, range(2))
    assert _pending(scenario, study) == [0, 1]
    assert no_direct == []
    assert spend.report(ledger)["calls"] == []


def test_changed_direct_commitment_refuses_before_admission(
    tmp_path: Path,
    scenario: Scenario,
    ledger: Path,
    commitment: Path,
    no_direct: list[str],
) -> None:
    study = tmp_path.resolve() / "study"
    register = run.seal(study, scenario.board, scenario.digest, "direct", None, ledger)
    assert run.load(study, scenario.digest) == register
    assert register["transport"] == "direct"
    commitment.write_text("0" * 64 + "\n", encoding="ascii")
    with pytest.raises(StudyError):
        _run(scenario, study, range(2))
    commitment.write_text(scenario.digest + "\n", encoding="ascii")
    assert _pending(scenario, study) == [0, 1]
    assert no_direct == []
    assert spend.report(ledger)["calls"] == []


@pytest.mark.usefixtures("commitment")
def test_direct_reserves_and_settles_each_draw_once_across_studies(
    tmp_path: Path, scenario: Scenario, ledger: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[tuple[str, str | None]] = []

    def request(path: str, body: bytes | None, _timeout: int) -> dict[str, Any]:
        if path == "/user/balance":
            assert body is None
            requests.append((path, None))
            return {
                "is_available": True,
                "balance_infos": [
                    {
                        "currency": "USD",
                        "total_balance": "2.00",
                        "granted_balance": "0.00",
                        "topped_up_balance": "2.00",
                    }
                ],
            }
        assert path == "/chat/completions" and body is not None
        messages = json.loads(body)["messages"]
        assert [message["role"] for message in messages] == ["system", "user"]
        requests.append((path, messages[1]["content"]))
        return {
            "model": deepseek.MODEL,
            "choices": [{"message": {"content": _DECISION}}],
            "usage": {
                "prompt_cache_hit_tokens": 2,
                "prompt_cache_miss_tokens": 3,
                "completion_tokens": 7,
                "prompt_tokens": 5,
                "total_tokens": 12,
            },
        }

    monkeypatch.setattr(deepseek, "_request", request)
    root = tmp_path.resolve()
    study, other = root / "study", root / "other"
    for target in (study, other):
        run.seal(target, scenario.board, scenario.digest, "direct", None, ledger)
    assert requests == []
    assert _pending(scenario, study) == [0, 1]
    assert _run(scenario, study, range(2)) == [
        {"index": 0, "state": "complete"},
        {"index": 1, "state": "complete"},
    ]
    _direct_env(monkeypatch, ledger)
    assert _pending(scenario, study) == _pending(scenario, other) == []
    for target in (study, other):
        assert _run(scenario, target, range(2)) == [
            {"index": 0, "state": "skipped_not_ready"},
            {"index": 1, "state": "skipped_not_ready"},
        ]
    assert (
        requests
        == [("/user/balance", None)]
        + [
            ("/user/balance", None),
            ("/chat/completions", scenario.prompt),
        ]
        * 2
    )
    usage = {
        "served_model": deepseek.MODEL,
        "input_tokens": 5,
        "cache_tokens": 2,
        "output_tokens": 7,
    }
    complete = [
        _row(scenario, index, "complete", turn=_received(scenario, _DECISION, usage))
        for index in range(2)
    ]
    assert _public(scenario, study) == _report(scenario, "direct", *complete)
    assert _public(scenario, other) == _report(
        scenario, "direct", _row(scenario, 0), _row(scenario, 1)
    )
    receipt = spend.report(ledger)
    assert receipt["unsettled_calls"] == 0
    assert [
        (
            item["run"],
            item["role"],
            item["cache_hit"],
            item["cache_miss"],
            item["output"],
        )
        for item in receipt["calls"]
    ] == [(row["run"], "dispatch", 2, 3, 7) for row in complete]


@pytest.mark.usefixtures("commitment")
def test_unsettled_ledger_reservation_blocks_ready_draw_without_claiming_it(
    tmp_path: Path, scenario: Scenario, ledger: Path, no_direct: list[str]
) -> None:
    study = tmp_path.resolve() / "study"
    run.seal(study, scenario.board, scenario.digest, "direct", None, ledger)
    first = _row(scenario, 0)
    spend.reserve(first["run"], "dispatch", balance_usd=_BALANCE)
    assert _pending(scenario, study) == [1]
    assert _run(scenario, study, range(1)) == [
        {"index": 0, "state": "skipped_not_ready"}
    ]
    assert _public(scenario, study) == _report(
        scenario, "direct", first, _row(scenario, 1)
    )
    receipt = spend.report(ledger)
    assert receipt["unsettled_calls"] == 1
    assert [item["run"] for item in receipt["calls"]] == [first["run"]]
    assert no_direct == []


@pytest.mark.usefixtures("commitment")
@pytest.mark.parametrize("replaced", ["directory", "database", "during-admission"])
def test_direct_rejects_replaced_ledger_identity_at_original_path(
    tmp_path: Path,
    scenario: Scenario,
    ledger: Path,
    no_direct: list[str],
    monkeypatch: pytest.MonkeyPatch,
    replaced: str,
) -> None:
    root = tmp_path.resolve()
    study = root / "study"
    run.seal(study, scenario.board, scenario.digest, "direct", None, ledger)
    database = spend._location(ledger)

    def replace_database() -> None:
        previous = database.rename(root / "previous-database")
        shutil.copy2(previous, database)

    if replaced == "directory":
        previous = ledger.rename(root / "previous-directory")
        ledger.mkdir(0o700)
        (previous / database.name).rename(database)
    elif replaced == "database":
        replace_database()
    else:
        monkeypatch.setattr(route_cli, "_admit", replace_database)
    with pytest.raises(StudyError):
        _run(scenario, study, range(2))
    assert no_direct == []
    assert spend.report(ledger)["calls"] == []


def test_commands_receive_only_exact_dispatch_prompt_and_report_no_usage(
    tmp_path: Path, scenario: Scenario, ledger: Path, no_direct: list[str]
) -> None:
    root = tmp_path.resolve()
    config, calls = _command_config(root, _DECISION)
    study = root / "study"
    run.seal(study, scenario.board, scenario.digest, "commands", config, None)
    assert run.load(study, scenario.digest)["transport"] == "commands"
    config.unlink()
    assert _run(scenario, study, range(1)) == [{"index": 0, "state": "complete"}]
    _command_calls(scenario, calls)
    complete = _row(scenario, 0, "complete", turn=_received(scenario, _DECISION))
    assert _public(scenario, study) == _report(
        scenario, "commands", complete, _row(scenario, 1)
    )
    assert _pending(scenario, study) == [1]
    assert spend.report(ledger)["calls"] == []
    assert no_direct == []


@pytest.mark.parametrize("name", ["DEEPSEEK_API_KEY", "SEATLAB_SPEND_DIR"])
def test_commands_cannot_allowlist_host_key_or_ledger(
    tmp_path: Path, scenario: Scenario, ledger: Path, no_direct: list[str], name: str
) -> None:
    root = tmp_path.resolve()
    config, calls = _command_config(root, _DECISION, env=(name,))
    with pytest.raises(StudyError):
        run.seal(
            root / "study", scenario.board, scenario.digest, "commands", config, None
        )
    assert not calls.exists()
    assert spend.report(ledger)["calls"] == []
    assert no_direct == []


@pytest.mark.parametrize(
    "broken", [False, True], ids=["invalid-decision", "callback-error"]
)
def test_commands_stop_on_fresh_incomplete_and_do_not_retry(
    tmp_path: Path, scenario: Scenario, ledger: Path, no_direct: list[str], broken: bool
) -> None:
    root = tmp_path.resolve()
    config, calls = _command_config(root, _PRIVATE_REPLY, broken=broken)
    study = root / "study"
    run.seal(study, scenario.board, scenario.digest, "commands", config, None)
    assert _run(scenario, study, range(2)) == [{"index": 0, "state": "incomplete"}]
    assert _pending(scenario, study) == [1]
    assert _run(scenario, study, range(1)) == [
        {"index": 0, "state": "skipped_not_ready"}
    ]
    _command_calls(scenario, calls)
    turn = (
        {
            "status": "callback_error",
            "prompt_sha256": _text_digest(scenario.prompt),
            "response_sha256": None,
            "usage": None,
        }
        if broken
        else _received(scenario, _PRIVATE_REPLY)
    )
    public = _public(scenario, study)
    assert public == _report(
        scenario,
        "commands",
        _row(scenario, 0, "incomplete", turn=turn),
        _row(scenario, 1),
    )
    encoded = json.dumps(public)
    assert all(
        private not in encoded for private in (_PRIVATE_REPLY, _TEST_KEY, str(root))
    )
    assert spend.report(ledger)["calls"] == []
    assert no_direct == []


def test_command_claim_precedes_callback_and_blocks_same_draw(
    tmp_path: Path,
    scenario: Scenario,
    monkeypatch: pytest.MonkeyPatch,
    no_direct: list[str],
) -> None:
    root = tmp_path.resolve()
    config, calls = _command_config(root, _DECISION)
    study = root / "study"
    run.seal(study, scenario.board, scenario.digest, "commands", config, None)
    entered, release = Event(), Event()
    invoke = seat_command.CommandSeat.__call__

    def pause(seat: seat_command.CommandSeat, prompt: str) -> str:
        entered.set()
        if not release.wait(timeout=_WAIT):
            raise TimeoutError("test callback wait")
        return invoke(seat, prompt)

    monkeypatch.setattr(seat_command.CommandSeat, "__call__", pause)
    with ThreadPoolExecutor(max_workers=2) as pool:
        winner = pool.submit(_run, scenario, study, range(1))
        try:
            assert entered.wait(timeout=_WAIT)
            loser = pool.submit(_run, scenario, study, range(1))
            assert loser.result(timeout=_WAIT) == [
                {"index": 0, "state": "skipped_not_ready"}
            ]
            assert _pending(scenario, study) == [1]
            assert _public(scenario, study) == _report(
                scenario, "commands", _row(scenario, 0, "incomplete"), _row(scenario, 1)
            )
        finally:
            release.set()
        assert winner.result(timeout=_WAIT) == [{"index": 0, "state": "complete"}]
    _command_calls(scenario, calls)
    assert no_direct == []
