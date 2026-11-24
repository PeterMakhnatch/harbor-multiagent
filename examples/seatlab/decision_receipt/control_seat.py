import asyncio
import hashlib
import logging
import shlex
from pathlib import Path
from typing import Any, override

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment, ExecResult
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

from . import control_client as client

AGENT_NAME = "decision-receipt-control"
AGENT_VERSION = "1.0.0"
SEAT = "relief"
METADATA_KEY = "decision_receipt_control"
RECORD_SCHEMA = "harbor.ship-decision-receipt.control/v1"
CLIENT_PATH = Path(__file__).resolve().with_name("control_client.py")
CLIENT_SHA256 = "be8c6e7726ebcf03b183a81948d0c321e909a37eff2f137df84590cb38a6f168"
CITE_ARGUMENTS = {True: "true", False: "false"}
HOST_TIMEOUT_SEC = 360.0
BINDING_REFUSED = "seat_binding_refused"
EXEC_FAILED = "client_exec_failed"
EXEC_TIMEOUT = "client_exec_timeout"
MALFORMED = "client_output_malformed"
OUTCOMES = frozenset(
    {*client.LITERALS, BINDING_REFUSED, EXEC_FAILED, EXEC_TIMEOUT, MALFORMED}
)

assert Path(client.__file__).resolve() == CLIENT_PATH
assert {text: flag for flag, text in CITE_ARGUMENTS.items()} == client.ARGUMENTS
assert len(OUTCOMES) == len(client.LITERALS) + 4
assert client.WAIT_SEC + client.POLL_SEC + 4 * client.REQUEST_SEC < HOST_TIMEOUT_SEC


class ControlFailure(RuntimeError):
    def __init__(self, outcome: str) -> None:
        if outcome not in OUTCOMES or outcome == client.SEALED:
            raise ValueError("unknown control failure")
        super().__init__(outcome)
        self.outcome = outcome


def client_source(path: Path) -> str:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != CLIENT_SHA256:
        raise ValueError("decision receipt control client differs from its pin")
    if not raw.isascii():
        raise ValueError("decision receipt control client is not ASCII")
    return raw.decode("ascii")


def command(cite: bool, source: str) -> str:
    if type(cite) is not bool:
        raise ValueError("decision receipt control cite must be a bool")
    if hashlib.sha256(source.encode("ascii")).hexdigest() != CLIENT_SHA256:
        raise ValueError("decision receipt control client differs from its pin")
    argv = ["python3", "-I", "-c", source, CITE_ARGUMENTS[cite]]
    return shlex.join(argv) + " </dev/null 2>/dev/null"


def outcome(result: ExecResult) -> str:
    stdout = result.stdout
    if type(stdout) is not str or stdout.count("\n") != 1:
        return MALFORMED
    literal, tail = stdout.split("\n")
    if tail or literal not in client.LITERALS:
        return MALFORMED
    if (result.return_code == 0) != (literal == client.SEALED):
        return MALFORMED
    return literal


class DecisionReceiptControl(BaseAgent):
    @staticmethod
    @override
    def name() -> str:
        return AGENT_NAME

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        cite: bool,
        logger: logging.Logger | None = None,
        extra_env: dict[str, str] | None = None,
        override_setup_timeout_sec: float | None = None,
        mcp_servers: list[Any] | None = None,
        skills_dir: str | None = None,
    ) -> None:
        if type(cite) is not bool:
            raise ValueError("DecisionReceiptControl cite must be true or false")
        if model_name not in (None, ""):
            raise ValueError("DecisionReceiptControl is scripted and takes no model")
        if extra_env:
            raise ValueError("DecisionReceiptControl forwards no environment")
        if mcp_servers or skills_dir is not None:
            raise ValueError("DecisionReceiptControl takes no tools or skills")
        self._cite = cite
        self._source = client_source(CLIENT_PATH)
        self._started = False
        super().__init__(
            logs_dir=logs_dir,
            model_name=None,
            logger=logger,
            override_setup_timeout_sec=override_setup_timeout_sec,
        )

    @override
    def version(self) -> str:
        return AGENT_VERSION

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        return None

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        record: dict[str, Any] = {
            "schema": RECORD_SCHEMA,
            "seat": SEAT,
            "cite": self._cite,
            "client_sha256": CLIENT_SHA256,
            "outcome": None,
        }
        context.metadata = {METADATA_KEY: record}
        if (
            self._started
            or not isinstance(environment, SeatEnvironment)
            or environment.service != SEAT
        ):
            record["outcome"] = BINDING_REFUSED
            raise ControlFailure(BINDING_REFUSED)
        self._started = True
        literal = await self._exec(environment)
        record["outcome"] = literal
        if literal != client.SEALED:
            raise ControlFailure(literal)

    async def _exec(self, environment: SeatEnvironment) -> str:
        line = command(self._cite, self._source)
        try:
            async with asyncio.timeout(HOST_TIMEOUT_SEC):
                result = await environment.exec(line)
        except TimeoutError:
            return EXEC_TIMEOUT
        except Exception:  # noqa: BLE001
            return EXEC_FAILED
        return outcome(result)
