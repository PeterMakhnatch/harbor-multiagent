"""CooperBench adapter: seat tasks, and the message service between seats."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
import urllib.error
import socket
import time
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from harbor.models.task.task import Task

ADAPTER_SRC = Path(__file__).parents[3] / "adapters/cooperbench/src"
sys.path.insert(0, str(ADAPTER_SRC))

from cooperbench import main as cli  # noqa: E402
from cooperbench.adapter import (  # noqa: E402
    CooperBenchAdapter,
    CooperBenchFeature,
    CooperBenchPair,
    _combined_patch_cache,
)

SEATS_TEMPLATE = ADAPTER_SRC / "cooperbench/task-template/seats"
COOP_MSG = SEATS_TEMPLATE / "environment/seat/coop_msg.py"

PATCH_A = """diff --git a/src/jinja2/loaders.py b/src/jinja2/loaders.py
--- a/src/jinja2/loaders.py
+++ b/src/jinja2/loaders.py
@@ -1,1 +1,2 @@
 import os
+VALIDATE = True
"""
PATCH_B = PATCH_A.replace("VALIDATE", "CACHE")
TESTS_A = PATCH_A.replace("src/jinja2/loaders.py", "tests/test_loader.py")
TESTS_B = PATCH_B.replace("src/jinja2/loaders.py", "tests/test_loader.py")


def make_pair(
    feature_a: str = "3",
    feature_b: str = "5",
    *,
    repo: str = "pallets/jinja",
    task_id: str = "1621",
    patch_a: str = PATCH_A,
) -> CooperBenchPair:
    def feature(fid: str, statement: str, patch: str, tests: str) -> CooperBenchFeature:
        return CooperBenchFeature(
            instance_id=f"{repo.replace('/', '__')}-{task_id}-{fid}",
            repo=repo,
            base_commit="0" * 40,
            problem_statement=statement,
            patch=patch,
            test_patch=tests,
            version="",
            environment_setup_commit="",
            hints_text=None,
            cooperbench_task_id=task_id,
            cooperbench_feature_id=fid,
        )

    return CooperBenchPair(
        repo=repo,
        task_id=task_id,
        base_commit="0" * 40,
        feature_a=feature(feature_a, "Add template path validation.", patch_a, TESTS_A),
        feature_b=feature(feature_b, "Add template path caching.", PATCH_B, TESTS_B),
    )


def generate(tmp_path: Path, pair: CooperBenchPair, *, seats: bool = True) -> Path:
    adapter = CooperBenchAdapter(output_dir=tmp_path / "tasks", seats=seats)
    return adapter.generate_task(pair)


def compose(task_dir: Path) -> dict[str, Any]:
    return yaml.safe_load((task_dir / "environment/docker-compose.yaml").read_text())


# -- seat task generation ---------------------------------------------------


def test_seat_task_names_two_seats_under_test(tmp_path: Path):
    task_dir = generate(tmp_path, make_pair())

    task = Task(task_dir)
    assert task.name == "cooperbench-seats/cb-jinja-t1621-f3-5"
    assert list(task.config.agents) == ["agent1", "agent2"]
    assert all(seat.under_test for seat in task.config.agents.values())
    assert task.config.metadata["seat_features"] == {"agent1": "3", "agent2": "5"}
    assert not (task_dir / "instruction.md").exists()


def test_each_seat_reads_its_own_feature_and_names_its_teammate(tmp_path: Path):
    task = Task(generate(tmp_path, make_pair()))

    agent1, agent2 = task.seat_instruction("agent1"), task.seat_instruction("agent2")
    assert agent1.startswith("Add template path validation.")
    assert "path caching" not in agent1
    assert agent2.startswith("Add template path caching.")
    assert "path validation" not in agent2
    assert "You are **agent1**. Your teammate **agent2**" in agent1
    assert "You are **agent2**. Your teammate **agent1**" in agent2
    for instruction in (agent1, agent2):
        assert "coop-send <recipient>" in instruction
        assert "coop-recv" in instruction
        for placeholder in ("{agent_id}", "{teammate}", "{feature_description}"):
            assert placeholder not in instruction


def test_compose_has_main_and_one_service_per_seat(tmp_path: Path):
    services = compose(generate(tmp_path, make_pair()))["services"]

    assert set(services) == {"main", "agent1", "agent2"}
    assert services["main"]["command"] == [
        "python3",
        "/opt/cooperbench/coop_server.py",
    ]
    for seat in ("agent1", "agent2"):
        assert services[seat]["build"] == {"context": "./seat"}
        assert services[seat]["environment"]["COOP_AGENT_ID"] == seat
        assert services[seat]["depends_on"]["main"]["condition"] == "service_healthy"


def test_each_seat_writes_only_its_own_workspace(tmp_path: Path):
    """No volume is shared by the two seats, and each volume a seat writes is
    read-only everywhere else, so neither seat can write the other's patch."""
    task_dir = generate(tmp_path, make_pair())
    services = compose(task_dir)["services"]

    mounts: dict[str, dict[str, bool]] = {}  # volume -> service -> writable
    for service, spec in services.items():
        for entry in spec.get("volumes", []):
            volume, target, *mode = entry.split(":")
            mounts.setdefault(volume, {})[service] = mode != ["ro"]
    for seat, other in (("agent1", "agent2"), ("agent2", "agent1")):
        assert f"{seat}-repo:/workspace/repo" in services[seat]["volumes"]
        assert f"{seat}-token:/run/cooperbench:ro" in services[seat]["volumes"]
        assert f"{seat}-repo:/agents/{seat}/repo:ro" in services["main"]["volumes"]
        assert not any(f"{other}-" in entry for entry in services[seat]["volumes"])
    for volume, users in mounts.items():
        assert not {"agent1", "agent2"} <= set(users), volume
        writers = [service for service, writable in users.items() if writable]
        assert len(writers) == 1, (volume, writers)


def test_solution_and_tests_follow_the_seats(tmp_path: Path):
    task_dir = generate(tmp_path, make_pair())

    assert (task_dir / "solution/agent1.patch").read_text() == PATCH_A
    assert (task_dir / "solution/agent2.patch").read_text() == PATCH_B
    assert (task_dir / "tests/tests1.patch").read_text() == TESTS_A
    assert (task_dir / "tests/tests2.patch").read_text() == TESTS_B
    assert not (task_dir / "solution/combined.patch").exists()
    for script in ("solution/solve.sh", "tests/test.sh"):
        assert os.access(task_dir / script, os.X_OK), script
    for dockerfile in ("environment/Dockerfile", "environment/seat/Dockerfile"):
        first = (task_dir / dockerfile).read_text().splitlines()[0]
        assert first == "FROM akhatua/cooperbench-pallets-jinja:task1621"


def test_sidecar_mode_keeps_its_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(
        _combined_patch_cache, ("pallets/jinja", "1621"), PATCH_A + PATCH_B
    )
    task_dir = generate(tmp_path, make_pair(), seats=False)

    assert (task_dir / "instruction.md").exists()
    assert not (task_dir / "instructions").exists()
    assert (task_dir / "environment/agent1/instruction.md").exists()
    assert (task_dir / "solution/combined.patch").exists()
    assert "[agents." not in (task_dir / "task.toml").read_text()


def test_overwrite_switches_a_task_between_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setitem(
        _combined_patch_cache, ("pallets/jinja", "1621"), PATCH_A + PATCH_B
    )
    out = tmp_path / "tasks"
    CooperBenchAdapter(output_dir=out).generate_task(make_pair())
    task_dir = CooperBenchAdapter(output_dir=out, seats=True).generate_task(
        make_pair(), overwrite=True
    )

    assert not (task_dir / "instruction.md").exists()
    assert not (task_dir / "environment/agent1").exists()
    assert Task(task_dir).config.agents


def test_seat_mode_takes_no_sidecar_harness():
    adapter = CooperBenchAdapter(output_dir=Path("/tmp"), seats=True, agent_harness="x")
    assert adapter.seats

    with pytest.raises(ValueError, match="Unknown agent harness"):
        CooperBenchAdapter(output_dir=Path("/tmp"), agent_harness="x")


@pytest.mark.parametrize(
    ("argv", "seats", "output_dir"),
    [
        ([], False, cli.DEFAULT_OUTPUT_DIR),
        (["--seats"], True, cli.DEFAULT_SEATS_OUTPUT_DIR),
        (["--seats", "--output-dir", "/tmp/x"], True, Path("/tmp/x")),
    ],
)
def test_cli_parses_seat_mode(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], seats: bool, output_dir: Path
):
    monkeypatch.setattr(sys, "argv", ["cooperbench", *argv])
    args = cli._parse_args()

    assert (args.seats, args.output_dir, args.agent_harness) == (
        seats,
        output_dir,
        "mini-swe-agent",
    )


def test_cli_refuses_a_sidecar_harness_with_seats(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        sys, "argv", ["cooperbench", "--seats", "--agent-harness", "openhands-sdk"]
    )
    with pytest.raises(SystemExit) as exit_info:
        cli._parse_args()
    assert exit_info.value.code == 2


def test_gold_patch_fixes_apply_to_the_seat_that_has_the_bug(tmp_path: Path):
    buggy = PATCH_A.replace("+VALIDATE = True", "+        min_instr_chars=30,")
    pair = make_pair("6", "5", repo="stanfordnlp/dspy", task_id="8635", patch_a=buggy)
    task_dir = generate(tmp_path, pair)

    assert "min_instr_chars=0," in (task_dir / "solution/agent1.patch").read_text()
    assert (task_dir / "solution/agent2.patch").read_text() == PATCH_B


@pytest.mark.parametrize(
    ("features", "fixed"), [(("1", "2"), True), (("2", "3"), False)]
)
def test_dspy_8563_test_fix_needs_feature_1_in_a_seat_task(
    tmp_path: Path, features: tuple[str, str], fixed: bool
):
    pair = make_pair(*features, repo="stanfordnlp/dspy", task_id="8563")
    task_dir = generate(tmp_path, pair)

    assert (task_dir / "tests/testfix.patch").exists() is fixed
    # The seat verifier appends testfix.patch itself; its test.sh is the template's.
    assert (task_dir / "tests/test.sh").read_bytes() == (
        SEATS_TEMPLATE / "tests/test.sh"
    ).read_bytes()


# react-hook-form 153, Feature 6's own gold patch: the type half of the fix
# matches, the implementation half does not (an excerpt of the real patch).
RHF_FEATURE_6_TYPE_HUNK = """diff --git a/src/types/form.ts b/src/types/form.ts
--- a/src/types/form.ts
+++ b/src/types/form.ts
@@ -628,4 +636,10 @@ export type UseFormHandleSubmit<
     : never,
-  onInvalid?: SubmitErrorHandler<TFieldValues>,
-) => (e?: React.BaseSyntheticEvent) => Promise<void>;
+  onInvalid?: SubmitErrorHandler<TFieldValues, TSubmitContext>,
+  options?: {
+    preventFocusOnError?: boolean;
+  },
+) => (
+  e?: React.BaseSyntheticEvent,
+  context?: TSubmitContext,
+) => Promise<void>;
 
"""
RHF_IMPLEMENTATION_HUNK = """diff --git a/src/logic/createFormControl.ts b/src/logic/createFormControl.ts
--- a/src/logic/createFormControl.ts
+++ b/src/logic/createFormControl.ts
@@ -1,2 +1,4 @@
 const handleSubmit =
+    (onValid, onInvalid, options) => async (e?, context?) => {
+      let onValidError: Error | undefined = undefined;
 };
"""


def test_rhf_153_fix_leaves_a_patch_with_only_one_half_alone(tmp_path: Path):
    pair = make_pair(
        "6",
        "5",
        repo="react-hook-form/react-hook-form",
        task_id="153",
        patch_a=RHF_FEATURE_6_TYPE_HUNK,
    )
    task_dir = generate(tmp_path, pair)

    patch = (task_dir / "solution/agent1.patch").read_text()
    assert patch == RHF_FEATURE_6_TYPE_HUNK
    assert "optionsOrOnFinally" not in patch


def test_rhf_153_fix_changes_both_halves_together(tmp_path: Path):
    both = RHF_IMPLEMENTATION_HUNK + RHF_FEATURE_6_TYPE_HUNK
    pair = make_pair(
        "6", "5", repo="react-hook-form/react-hook-form", task_id="153", patch_a=both
    )
    task_dir = generate(tmp_path, pair)

    patch = (task_dir / "solution/agent1.patch").read_text()
    assert "const options = typeof optionsOrOnFinally === 'function'" in patch
    assert "+  optionsOrOnFinally?: ((formState" in patch
    assert "@@ -1,2 +1,9 @@" in patch  # 5 more lines in the implementation hunk


# -- the message service in main, and the seats' commands -------------------


def load_server() -> ModuleType:
    path = SEATS_TEMPLATE / "environment/coop_server.py"
    spec = importlib.util.spec_from_file_location("coop_server", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def serving(
    root: Path, module: ModuleType | None = None, **limits: int
) -> Iterator[dict[str, Any]]:
    tokens_dir = root / "tokens"
    for seat in ("agent1", "agent2"):
        (tokens_dir / seat).mkdir(parents=True, exist_ok=True)
    server = (module or load_server()).make_server(
        ["agent1", "agent2"],
        tokens_dir,
        root / "logs",
        host="127.0.0.1",
        port=0,
        **limits,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {
            "url": f"http://127.0.0.1:{server.server_address[1]}",
            "port": server.server_address[1],
            "token_files": {s: tokens_dir / s / "token" for s in ("agent1", "agent2")},
            "logs": root / "logs",
        }
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def service(tmp_path: Path) -> Iterator[dict[str, Any]]:
    with serving(tmp_path) as info:
        yield info


def call(service: dict[str, Any], method: str, path: str, seat: str | None, body=None):
    request = urllib.request.Request(
        service["url"] + path,
        data=None if body is None else json.dumps(body).encode(),
        method=method,
    )
    if seat is not None:
        token = service["token_files"][seat].read_text().strip()
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_service_writes_one_token_per_seat(service: dict[str, Any]):
    tokens = {
        seat: path.read_text().strip() for seat, path in service["token_files"].items()
    }

    assert len(set(tokens.values())) == 2
    assert all(len(token) == 32 for token in tokens.values())


def test_service_refuses_a_start_without_a_seat_token_directory(tmp_path: Path):
    (tmp_path / "tokens" / "agent1").mkdir(parents=True)

    with pytest.raises(SystemExit, match="no token directory for agent2"):
        load_server().make_server(
            ["agent1", "agent2"], tmp_path / "tokens", tmp_path / "logs", port=0
        )


def test_the_sender_is_the_token_holder(service: dict[str, Any]):
    status, sent = call(
        service,
        "POST",
        "/send",
        "agent1",
        {"to": "agent2", "content": "I edit loaders.py"},
    )
    assert status == 201
    assert sent["from"] == "agent1"

    status, reply = call(
        service,
        "POST",
        "/send",
        "agent1",
        {"to": "agent2", "from": "agent2", "content": "x"},
    )
    assert status == 403
    assert reply["error"] == "this token is agent1's"
    assert (
        call(service, "POST", "/send", None, {"to": "agent2", "content": "x"})[0] == 401
    )
    assert (
        call(service, "POST", "/send", "agent1", {"to": "agent3", "content": "x"})[0]
        == 400
    )


def test_a_receive_drains_only_the_callers_inbox(service: dict[str, Any]):
    call(service, "POST", "/send", "agent1", {"to": "agent2", "content": "first"})
    call(service, "POST", "/send", "agent1", {"to": "agent2", "content": "second"})

    assert call(service, "POST", "/recv", "agent1", {}) == (200, {"messages": []})
    assert call(service, "GET", "/peek", "agent2") == (200, {"unread": 2})
    status, reply = call(service, "POST", "/recv", "agent2", {})
    assert status == 200
    assert [m["content"] for m in reply["messages"]] == ["first", "second"]
    assert {m["from"] for m in reply["messages"]} == {"agent1"}
    assert call(service, "POST", "/recv", "agent2", {}) == (200, {"messages": []})
    assert call(service, "POST", "/recv", None, {})[0] == 401


def test_the_service_logs_each_message_receive_and_bad_token(service: dict[str, Any]):
    call(service, "POST", "/send", "agent1", {"to": "agent2", "content": "hello"})
    call(service, "POST", "/recv", "agent2", {})
    call(service, "POST", "/recv", None, {})

    messages = [
        json.loads(line)
        for line in (service["logs"] / "messages.jsonl").read_text().splitlines()
    ]
    events = [
        json.loads(line)
        for line in (service["logs"] / "events.jsonl").read_text().splitlines()
    ]
    assert [(m["id"], m["from"], m["to"], m["content"]) for m in messages] == [
        ("m1", "agent1", "agent2", "hello")
    ]
    assert [(e["seat"], e["kind"]) for e in events] == [
        ("agent1", "send"),
        ("agent2", "recv"),
        (None, "bad_token"),
    ]
    assert events[1]["data"] == {"ids": ["m1"]}


def coop(
    service: dict[str, Any], seat: str, *args: str
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "COOP_URL": service["url"],
        "COOP_TOKEN_FILE": str(service["token_files"][seat]),
    }
    return subprocess.run(
        [sys.executable, str(COOP_MSG), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_seat_commands_match_upstream_cooperbench(service: dict[str, Any]):
    sent = coop(service, "agent1", "send", "agent2", "I plan to modify: loaders.py")
    assert (sent.returncode, sent.stderr.strip()) == (0, "sent to agent2")
    assert coop(service, "agent2", "peek").stdout.strip() == "1"
    assert coop(service, "agent2", "agents").stdout.split() == ["agent1", "agent2"]

    received = json.loads(coop(service, "agent2", "recv").stdout)
    assert [(m["from"], m["to"], m["content"]) for m in received] == [
        ("agent1", "agent2", "I plan to modify: loaders.py")
    ]
    assert {"timestamp", "timestamp_iso"} <= set(received[0])

    assert coop(service, "agent2", "broadcast", "Done.").returncode == 0
    assert [
        m["content"] for m in json.loads(coop(service, "agent1", "recv").stdout)
    ] == ["Done."]
    assert json.loads(coop(service, "agent2", "recv").stdout) == []


def test_seat_commands_fail_loudly_on_a_bad_token(
    service: dict[str, Any], tmp_path: Path
):
    forged = tmp_path / "forged-token"
    forged.write_text("0" * 32)
    service = {**service, "token_files": {**service["token_files"], "agent1": forged}}

    result = coop(service, "agent1", "send", "agent2", "hello")
    assert result.returncode == 1
    assert "HTTP 401" in result.stderr


def test_a_restart_keeps_the_seats_tokens(tmp_path: Path):
    with serving(tmp_path) as first:
        tokens = {s: p.read_text() for s, p in first["token_files"].items()}
    with serving(tmp_path) as second:
        assert {s: p.read_text() for s, p in second["token_files"].items()} == tokens
        assert call(second, "GET", "/peek", "agent1") == (200, {"unread": 0})


def test_the_message_cap_holds_under_concurrent_sends(tmp_path: Path):
    with serving(tmp_path, max_messages=5) as info:
        statuses: list[int] = []

        def send() -> None:
            statuses.append(
                call(info, "POST", "/send", "agent1", {"to": "agent2", "content": "x"})[
                    0
                ]
            )

        threads = [threading.Thread(target=send) for _ in range(40)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    assert sorted(statuses) == [201] * 5 + [429] * 35


def test_messages_are_capped_in_utf8_bytes(tmp_path: Path):
    with serving(tmp_path, max_bytes=100) as info:
        emoji = "\U0001f600"  # 4 bytes in UTF-8
        too_long = emoji * (65_536 // 4 + 1)
        assert (
            call(
                info, "POST", "/send", "agent1", {"to": "agent2", "content": too_long}
            )[0]
            == 413
        )
        assert (
            call(
                info, "POST", "/send", "agent1", {"to": "agent2", "content": emoji * 20}
            )[0]
            == 201
        )
        status, reply = call(
            info, "POST", "/send", "agent1", {"to": "agent2", "content": emoji * 6}
        )
        assert status == 429
        assert "at most 100 bytes" in reply["error"]
        line = (info["logs"] / "messages.jsonl").read_bytes().splitlines()[0]
        assert emoji.encode() in line  # stored as UTF-8, not escaped


def test_events_stop_at_their_cap(tmp_path: Path):
    with serving(tmp_path, max_events=3) as info:
        for _ in range(5):
            call(info, "POST", "/recv", None, {})
        events = (info["logs"] / "events.jsonl").read_text().splitlines()

    assert [json.loads(line)["kind"] for line in events] == [
        "bad_token",
        "bad_token",
        "events_capped",
    ]


@pytest.mark.parametrize("recipient", [[], {}, 7, None])
def test_a_recipient_that_is_not_a_seat_name_is_refused(
    service: dict[str, Any], recipient: object
):
    status, reply = call(
        service, "POST", "/send", "agent1", {"to": recipient, "content": "x"}
    )
    assert status == 400
    assert reply["error"].startswith("'to' must be one of")


def test_stalled_connections_time_out_and_are_capped(tmp_path: Path):
    module = load_server()
    setattr(module, "REQUEST_TIMEOUT_SEC", 1)
    with serving(tmp_path, module, max_connections=2) as info:
        token = info["token_files"]["agent1"].read_text().strip()
        stalled = []
        for _ in range(2):  # each promises a body it never sends
            conn = socket.create_connection(("127.0.0.1", info["port"]))
            conn.sendall(
                b"POST /send HTTP/1.0\r\nAuthorization: Bearer "
                + token.encode()
                + b"\r\nContent-Length: 100\r\n\r\n"
            )
            stalled.append(conn)
        time.sleep(0.3)
        extra = socket.create_connection(("127.0.0.1", info["port"]))
        extra.settimeout(5)
        extra.sendall(b"GET /health HTTP/1.0\r\n\r\n")
        try:  # closed without a reply, both slots busy: EOF or a reset
            reply = extra.recv(100)
        except ConnectionResetError:
            reply = b""
        assert reply == b""
        extra.close()
        time.sleep(1.5)  # the stalled reads time out and free their slots
        assert call(info, "GET", "/peek", "agent1") == (200, {"unread": 0})
        for conn in stalled:
            conn.close()
