import hashlib
import json
import shlex
from pathlib import Path
from typing import Any, override

import pytest

from examples.seatlab.decision_receipt import control_client as client
from examples.seatlab.decision_receipt import control_seat as seat
from harbor.environments.base import ExecResult
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

REFERENCE = "0123456789abcdef0123456789abcdef"
BRIEF = (200, b'{"role":"relief","receipt":"unconfirmed"}')
WAITING = (200, b'{"handoff_ready":false,"relief_sealed":false}')
READY = (200, b'{"handoff_ready":true,"relief_sealed":false}')
INBOX = (200, json.dumps({"ready": True, "reference": REFERENCE}).encode())
ACCEPTED = (200, b'{"ok":true}')

Exchange = tuple[str, str, tuple[int, bytes] | type[Exception]]


class Script:
    def __init__(self, exchanges: list[Exchange]) -> None:
        self.exchanges = list(exchanges)
        self.calls: list[tuple[str, str, bytes | None]] = []
        self.now = 0.0
        self.slept: list[float] = []

    def call(self, method: str, path: str, data: bytes | None) -> tuple[int, bytes]:
        assert (method, path) in client.ROUTES
        assert (data is None) == (method == "GET")
        assert self.exchanges, f"unexpected {method} {path}"
        want_method, want_path, reply = self.exchanges.pop(0)
        assert (method, path) == (want_method, want_path)
        self.calls.append((method, path, data))
        self.now += 0.1
        if isinstance(reply, type):
            raise reply()
        return reply

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def run(self, cite: bool) -> str:
        try:
            return client.run(cite, self.call, self.clock, self.sleep)
        except client.Failure as failure:
            return failure.literal

    def posts(self) -> list[bytes | None]:
        return [data for method, _, data in self.calls if method == "POST"]


def happy(resolve: tuple[int, bytes] | type[Exception]) -> list[Exchange]:
    return [
        ("GET", "/brief", BRIEF),
        ("GET", "/status", WAITING),
        ("GET", "/status", WAITING),
        ("GET", "/status", READY),
        ("GET", "/inbox", INBOX),
        ("POST", "/resolve", resolve),
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("cite", "body"),
    [
        (False, {"action": "close"}),
        (True, {"action": "close", "cite": REFERENCE}),
    ],
)
def test_relief_waits_for_reference_then_closes_once(
    cite: bool, body: dict[str, str]
) -> None:
    script = Script(happy(ACCEPTED))
    assert script.run(cite) == client.SEALED
    assert script.exchanges == []
    assert [path for _, path, _ in script.calls] == [
        "/brief",
        "/status",
        "/status",
        "/status",
        "/inbox",
        "/resolve",
    ]
    assert script.posts() == [json.dumps(body, separators=(",", ":")).encode()]
    assert script.slept == [client.POLL_SEC, client.POLL_SEC]
    assert all(seconds >= 2.0 for seconds in script.slept)


@pytest.mark.unit
def test_cite_changes_only_the_cite_field_at_commit() -> None:
    plain = json.loads(client.resolution(False, REFERENCE))
    cited = json.loads(client.resolution(True, REFERENCE))
    assert plain == {"action": "close"}
    assert cited == {**plain, "cite": REFERENCE}
    with pytest.raises(ValueError):
        client.resolution(True, REFERENCE.upper())
    with pytest.raises(ValueError):
        client.resolution(1, REFERENCE)  # type: ignore


@pytest.mark.unit
@pytest.mark.parametrize(
    "reply",
    [
        {"ready": True, "reference": None},
        {"ready": False, "reference": None},
        {"ready": 1, "reference": REFERENCE},
        {"ready": True, "reference": REFERENCE.upper()},
        {"ready": True, "reference": REFERENCE[:-1]},
        {"ready": True, "reference": REFERENCE, "extra": 1},
    ],
)
def test_inbox_without_private_reference_never_posts(reply: dict[str, object]) -> None:
    exchanges = happy(ACCEPTED)[:-1]
    exchanges[-1] = ("GET", "/inbox", (200, json.dumps(reply).encode()))
    script = Script(exchanges)
    assert script.run(True) == "inbox_refused"
    assert script.posts() == []


@pytest.mark.unit
def test_inbox_is_read_once_and_not_retried() -> None:
    exchanges = happy(ACCEPTED)[:-1]
    exchanges[-1] = ("GET", "/inbox", client.Unreachable)
    script = Script(exchanges)
    assert script.run(False) == "transport_failed"
    assert [path for _, path, _ in script.calls].count("/inbox") == 1
    assert script.posts() == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "brief",
    [
        (200, b'{"role":"outgoing","receipt":"unconfirmed"}'),
        (200, b'{"role":"relief","receipt":"unconfirmed","request_ref":"x"}'),
        (200, b'{"role":"relief","role":"relief","receipt":"unconfirmed"}'),
        (403, b'{"error":"forbidden"}'),
    ],
)
def test_wrong_brief_stops_before_status(brief: tuple[int, bytes]) -> None:
    script = Script([("GET", "/brief", brief)])
    assert script.run(False) == "brief_refused"
    assert len(script.calls) == 1


@pytest.mark.unit
def test_unreachable_world_is_retried_for_brief_only_on_cadence() -> None:
    script = Script(
        [
            ("GET", "/brief", client.Unreachable),
            ("GET", "/brief", BRIEF),
            ("GET", "/status", READY),
            ("GET", "/inbox", INBOX),
            ("POST", "/resolve", ACCEPTED),
        ]
    )
    assert script.run(False) == client.SEALED
    assert script.slept == [client.POLL_SEC]


@pytest.mark.unit
@pytest.mark.parametrize(
    "status",
    [
        (200, b'{"handoff_ready":true,"relief_sealed":true}'),
        (200, b'{"handoff_ready":1,"relief_sealed":false}'),
        (200, b'{"handoff_ready":true}'),
        (200, b'{"handoff_ready":NaN,"relief_sealed":false}'),
        (500, b'{"error":"internal"}'),
    ],
)
def test_malformed_or_sealed_status_never_posts(status: tuple[int, bytes]) -> None:
    script = Script([("GET", "/brief", BRIEF), ("GET", "/status", status)])
    assert script.run(True) == "status_refused"
    assert script.posts() == []


@pytest.mark.unit
def test_missing_handoff_times_out_within_bound() -> None:
    exchanges: list[Exchange] = [("GET", "/brief", BRIEF)]
    exchanges += [("GET", "/status", WAITING)] * client.MAX_POLLS
    script = Script(exchanges)
    assert script.run(True) == "handoff_timeout"
    polls = [path for _, path, _ in script.calls].count("/status")
    assert 2 <= polls <= client.MAX_POLLS
    assert all(seconds == client.POLL_SEC for seconds in script.slept)
    assert client.WAIT_SEC <= script.now < client.WAIT_SEC + 2 * client.POLL_SEC
    assert script.posts() == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("resolve", "literal"),
    [
        ((400, b'{"error":"unknown reference"}'), "resolve_refused"),
        ((409, b'{"error":"already resolved"}'), "resolve_refused"),
        ((200, b'{"ok":1}'), "resolve_unconfirmed"),
        ((200, b"not json"), "resolve_unconfirmed"),
        (client.Unanswered, "resolve_unconfirmed"),
        (client.Unreachable, "resolve_unsent"),
    ],
)
def test_resolve_is_posted_exactly_once_without_retry(
    resolve: tuple[int, bytes] | type[Exception], literal: str
) -> None:
    script = Script(happy(resolve))
    assert script.run(True) == literal
    assert len(script.posts()) == 1
    assert script.exchanges == []


@pytest.mark.unit
def test_decode_refuses_duplicate_keys_constants_and_oversize() -> None:
    assert client.decode(b'{"ok":true}') == {"ok": True}
    assert client.decode(b'{"ok":true,"ok":true}') is None
    assert client.decode(b'{"x":Infinity}') is None
    assert client.decode(b"\xff") is None
    assert client.decode(b" " * (client.MAX_REPLY_BYTES + 1)) is None


@pytest.mark.unit
def test_client_routes_and_literals_are_closed() -> None:
    assert client.ROUTES == {
        ("GET", "/brief"),
        ("GET", "/status"),
        ("GET", "/inbox"),
        ("POST", "/resolve"),
    }
    assert all(literal.isascii() and " " not in literal for literal in client.LITERALS)
    with pytest.raises(ValueError):
        client.Failure("unknown reference")


@pytest.mark.unit
@pytest.mark.parametrize("argv", [[], ["True"], ["1"], ["true", "false"]])
def test_client_takes_one_literal_argument(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert client.main(argv) == 1
    captured = capsys.readouterr()
    assert captured.out == "input_refused\n" and captured.err == ""


@pytest.mark.unit
def test_client_source_matches_pin() -> None:
    raw = seat.CLIENT_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == seat.CLIENT_SHA256
    assert seat.client_source(seat.CLIENT_PATH) == raw.decode("ascii")


@pytest.mark.unit
@pytest.mark.parametrize(("cite", "argument"), [(True, "true"), (False, "false")])
def test_command_runs_pinned_source_with_fixed_literal(
    cite: bool, argument: str
) -> None:
    source = seat.client_source(seat.CLIENT_PATH)
    words = shlex.split(seat.command(cite, source))
    assert words[:4] == ["python3", "-I", "-c", source]
    assert words[4:] == [argument, "</dev/null", "2>/dev/null"]
    with pytest.raises(ValueError):
        seat.command(cite, source + "\n")
    with pytest.raises(ValueError):
        seat.command("true", source)  # type: ignore


@pytest.mark.unit
@pytest.mark.parametrize(
    ("stdout", "code", "literal"),
    [
        ("sealed\n", 0, "sealed"),
        ("handoff_timeout\n", 1, "handoff_timeout"),
        ("sealed\n", 1, seat.MALFORMED),
        ("inbox_refused\n", 0, seat.MALFORMED),
        ("sealed", 0, seat.MALFORMED),
        ("sealed\nsealed\n", 0, seat.MALFORMED),
        (REFERENCE + "\n", 1, seat.MALFORMED),
        (None, 0, seat.MALFORMED),
    ],
)
def test_host_reads_only_fixed_literals(
    stdout: str | None, code: int, literal: str
) -> None:
    assert seat.outcome(ExecResult(stdout=stdout, return_code=code)) == literal


@pytest.mark.unit
@pytest.mark.parametrize(
    "options",
    [
        {"cite": "true"},
        {"cite": 1},
        {"cite": None},
        {"cite": True, "model_name": "deepseek/deepseek-chat"},
        {"cite": True, "extra_env": {"SH_POLICY": "close"}},
        {"cite": True, "skills_dir": "/skills"},
    ],
)
def test_control_refuses_model_env_and_tools(
    tmp_path: Path, options: dict[str, Any]
) -> None:
    with pytest.raises(ValueError):
        seat.DecisionReceiptControl(logs_dir=tmp_path, **options)


@pytest.mark.unit
@pytest.mark.parametrize(
    "extra",
    [
        {"policy": "close"},
        {"base_url": "http://example.invalid"},
        {"model_api_key_name": "KEY"},
        {"skip_model_parsing": True},
    ],
)
def test_control_refuses_other_kwargs(tmp_path: Path, extra: dict[str, Any]) -> None:
    with pytest.raises(TypeError):
        seat.DecisionReceiptControl(logs_dir=tmp_path, cite=True, **extra)


@pytest.mark.unit
def test_control_identity(tmp_path: Path) -> None:
    agent = seat.DecisionReceiptControl(logs_dir=tmp_path, cite=False)
    assert seat.DecisionReceiptControl.name() == "decision-receipt-control"
    assert agent.version() == "1.0.0"
    assert agent.model_name is None


class ExecBoundary(SeatEnvironment):
    def __init__(self, service: str, reply: ExecResult | Exception) -> None:
        self.service = service
        self.reply = reply
        self.calls: list[tuple[str, dict[str, object]]] = []

    @override
    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult:
        self.calls.append(
            (
                command,
                {"cwd": cwd, "env": env, "timeout_sec": timeout_sec, "user": user},
            )
        )
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.mark.unit
@pytest.mark.parametrize("cite", [True, False])
async def test_run_execs_client_once_and_records_literal(
    tmp_path: Path, cite: bool
) -> None:
    agent = seat.DecisionReceiptControl(logs_dir=tmp_path, cite=cite)
    environment = ExecBoundary("relief", ExecResult(stdout="sealed\n", return_code=0))
    context = AgentContext()
    await agent.run("ignored", environment, context)
    source = seat.client_source(seat.CLIENT_PATH)
    assert environment.calls == [
        (
            seat.command(cite, source),
            {
                "cwd": None,
                "env": None,
                "timeout_sec": None,
                "user": None,
            },
        )
    ]
    assert context.metadata == {
        seat.METADATA_KEY: {
            "schema": seat.RECORD_SCHEMA,
            "seat": "relief",
            "cite": cite,
            "client_sha256": seat.CLIENT_SHA256,
            "outcome": "sealed",
        }
    }
    with pytest.raises(seat.ControlFailure, match=seat.BINDING_REFUSED):
        await agent.run("ignored", environment, AgentContext())
    assert len(environment.calls) == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    ("reply", "literal"),
    [
        (ExecResult(stdout="handoff_timeout\n", return_code=1), "handoff_timeout"),
        (ExecResult(stdout=REFERENCE + "\n", return_code=0), seat.MALFORMED),
        (RuntimeError("Bearer secret " + REFERENCE), seat.EXEC_FAILED),
        (TimeoutError(), seat.EXEC_TIMEOUT),
    ],
)
async def test_failed_client_raises_only_literal(
    tmp_path: Path, reply: ExecResult | Exception, literal: str
) -> None:
    agent = seat.DecisionReceiptControl(logs_dir=tmp_path, cite=True)
    context = AgentContext()
    with pytest.raises(seat.ControlFailure) as failure:
        await agent.run("ignored", ExecBoundary("relief", reply), context)
    assert str(failure.value) == literal
    assert context.metadata is not None
    assert context.metadata[seat.METADATA_KEY]["outcome"] == literal
    assert REFERENCE not in json.dumps(context.metadata)


@pytest.mark.unit
async def test_run_refuses_any_seat_but_relief(tmp_path: Path) -> None:
    agent = seat.DecisionReceiptControl(logs_dir=tmp_path, cite=True)
    environment = ExecBoundary("outgoing", ExecResult(stdout="sealed\n", return_code=0))
    with pytest.raises(seat.ControlFailure, match=seat.BINDING_REFUSED):
        await agent.run("ignored", environment, AgentContext())
    assert environment.calls == []
