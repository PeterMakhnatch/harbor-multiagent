import asyncio
import sys
import tempfile
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from ... import native_owner_process as transport
from ...joint_reopen import owner_game, scripted_owner
from ...native_evidence import Record
from ..partner_switch.engine import native
from .diagnose import panel
from .plan import CASES, SCRIPTED_MODES

SCHEMA = "seatlab.partner-native-rehearsal/v1"
PAIRS = (("p00-00", "p00-01"), ("p01-00", "p01-01"))
OWNER_SECONDS = 10.0
WORLD_SECONDS = 60.0
SCOPE = (
    "In-process native Joint Reopen worlds with bundled scripted owners only. "
    "The same six calendars recur in all four ordered pairings; the 24 worlds "
    "and 240 private decisions are correlated opportunities, not IID samples. "
    "No population inference, Harbor trials, sandbox or model qualification."
)


async def _owner_choice(role: str, requested: str, view: owner_game.Object) -> str:
    payload = transport._request(
        scripted_owner.REQUEST_SCHEMA, requested, owner_game.PUBLIC_INSTRUCTIONS, view
    )
    executable = Path(__file__).resolve().with_name("owner.py")
    if not Path(sys.executable).is_absolute() or not executable.is_file():
        raise ValueError("bundled owner or absolute Python executable unavailable")
    with tempfile.TemporaryDirectory(prefix="partner-native-owner-") as cwd:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-IB",
            "-S",
            str(executable),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env={"LANG": "C", "LC_ALL": "C", "HOME": cwd, "TMPDIR": cwd},
            close_fds=True,
            start_new_session=True,
        )
        try:
            async with asyncio.timeout(OWNER_SECONDS):
                stdout, code = await transport._exchange(role, process, payload)
        except BaseException:
            await transport._reap(role, process)
            raise
    if code != 0:
        raise ValueError("bundled owner exited unsuccessfully")
    reply = transport._reply(role, scripted_owner.REPLY_SCHEMA, stdout)
    choice = reply["choice"]
    if (
        type(reply["requested"]) is not str
        or reply["requested"] != requested
        or reply["served_model"] is not None
        or type(choice) is not str
        or choice not in transport._offered(view)
    ):
        raise ValueError("bundled owner reply differs from the native turn contract")
    return choice


@dataclass
class _Role:
    role: str
    requested: str
    world: native._Meeting
    barriers: dict[str, asyncio.Barrier]
    decisions: int = 0
    confirmed: list[str] = field(default_factory=list)
    card: tuple[str, ...] = ()
    report: tuple[str, ...] = ()
    inbox: tuple[str, ...] = ()

    async def request(
        self, path: str, body: owner_game.Object | None
    ) -> tuple[int, owner_game.Object]:
        expected = ("/endorse", "/card", "/report", "/inbox", "/vote")
        if (
            len(self.confirmed) >= len(expected)
            or path != expected[len(self.confirmed)]
        ):
            raise ValueError("unexpected scripted world request order")
        status, result = self.world.handle(
            self.role,
            "GET" if body is None else "POST",
            path,
            {} if body is None else body,
        )
        if status != 200:
            raise ValueError(f"native world rejected {path}")
        if path == "/card":
            self.card = owner_game.slot_list(result.get("slots"), 2, 2, path)
        elif path == "/report":
            if body is None:
                raise ValueError("missing singleton report")
            self.report = owner_game.slot_list(body.get("slots"), 1, 1, path)
            if self.report[0] not in self.card:
                raise ValueError(
                    "singleton report is not on the originally observed card"
                )
        elif path == "/inbox":
            if result.get("delivered") is not True:
                raise ValueError("native inbox did not deliver the peer report")
            self.inbox = owner_game.slot_list(result.get("slots"), 1, 1, path)
        self.confirmed.append(path)
        return status, result

    async def decide(self, view: owner_game.Object) -> str:
        if (
            view.get("role") != self.role
            or view.get("turn") != self.decisions + 1
            or self.decisions >= owner_game.MAX_TURNS
        ):
            raise ValueError("scripted role view is out of bounds")
        choice = await _owner_choice(self.role, self.requested, view)
        self.decisions += 1
        return choice

    async def wait(self, phase: str) -> None:
        await self.barriers[phase].wait()
        if self.world.handle(self.role, "GET", "/status", {}) != (
            200,
            {"phase": phase},
        ):
            raise ValueError("native world did not complete the preceding phase")

    async def play(self) -> None:
        status, brief = self.world.handle(self.role, "GET", "/brief", {})
        if status != 200 or brief.get("role") != self.role:
            raise ValueError("native world rejected the role brief")
        summary = await owner_game.play(brief, self.request, self.decide, self.wait)
        if summary != {"role": self.role, "terminal": "vote", "turns": 5}:
            raise ValueError("scripted role did not finish all five turns")
        if self.decisions != 5 or len(self.confirmed) != 5:
            raise ValueError("scripted role has incomplete decisions or receipts")


def _verify_world(world: native._Meeting, seats: list[_Role]) -> Record:
    cut = world.freeze()
    try:
        observed = native._verify.evaluate(cut)
    except native._verify.CutError as error:
        raise ValueError("invalid native world cut") from error
    if (
        observed["initial_joint"] is not True
        or observed["incumbent_feasible"] is not False
    ):
        raise ValueError("native world is not a jointly invalid incumbent case")
    roles = observed["roles"]
    cards = cast(dict[str, list[str]], cut["cards"])
    events = cast(list[dict[str, Any]], cut["events"])
    if len(events) != 10:
        raise ValueError("native world has incomplete event receipts")
    for seat, peer in zip(seats, reversed(seats), strict=True):
        summary = roles[seat.role]
        reports = [
            event["value"]
            for event in events
            if event["role"] == seat.role and event["kind"] == "report"
        ]
        if (
            summary["card_before_vote"] is not True
            or summary["report_after_card"] is not True
            or summary["peer_report_before_vote"] is not True
            or summary["report_true"] is not False
            or summary["voted"] is not True
            or seat.inbox != peer.report
            or cards[seat.role] != list(seat.card)
            or reports != [list(seat.report)]
        ):
            raise ValueError("native observation differs from scripted role receipts")
    reward = observed["reward"]
    if type(reward) is not int or reward not in (0, 1):
        raise ValueError("native verifier did not return a binary reward")
    return observed


async def _episode(cell: Record, team: list[Record]) -> tuple[Record, int]:
    world = native.Meeting(cell["seed"], cell["fixture"], cell["channel"], str(uuid4()))
    barriers = {phase: asyncio.Barrier(2) for phase in owner_game.WAIT_PHASES}
    seats = [
        _Role(member["seat"], member["model_name"], world, barriers) for member in team
    ]
    if tuple(seat.role for seat in seats) != owner_game.ROLES:
        raise ValueError("native team does not name the two roles in order")
    try:
        async with asyncio.timeout(WORLD_SECONDS):
            async with asyncio.TaskGroup() as group:
                for seat in seats:
                    group.create_task(seat.play())
    except (ExceptionGroup, TimeoutError) as error:
        raise ValueError(
            "scripted native world failed or exceeded its deadline"
        ) from error
    return _verify_world(world, seats), sum(seat.decisions for seat in seats)


def _registered_pairs(registration: Record) -> list[list[list[Record]]]:
    pairs: dict[str, list[Record]] = {pair: [] for row in PAIRS for pair in row}
    cells = registration["cells"]
    if len(cells) != 4 * CASES or set(registration["teams"]) != set(pairs):
        raise ValueError(
            "native rehearsal requires 24 registered worlds in four pairings"
        )
    for cell in cells:
        if cell["team"] not in pairs or (cell["fixture"], cell["channel"]) != (
            "both",
            "deliver",
        ):
            raise ValueError("unexpected registered world condition")
        pairs[cell["team"]].append(cell)
    seeds = {cell["seed"] for cell in pairs[PAIRS[0][0]]}
    if len(seeds) != CASES or any(
        len(group) != CASES or {cell["seed"] for cell in group} != seeds
        for group in pairs.values()
    ):
        raise ValueError("all four pairings must share the same six distinct calendars")
    return [[pairs[pair] for pair in row] for row in PAIRS]


def _offsetting_prediction(matrix: list[list[str]], diagnosis: Record) -> bool:
    if (
        matrix != [["1/2", "1/2"], ["1/2", "1/2"]]
        or not diagnosis["complete"]
        or diagnosis["registered_worlds"] != 4 * CASES
        or diagnosis["observed_worlds"] != 4 * CASES
    ):
        return False
    names = diagnosis["names"]
    expected = {
        (anchor, role, partner)
        for anchor in names
        for partner in names
        if anchor != partner
        for role in owner_game.ROLES
    }
    contrasts = diagnosis["contrasts"]
    if (
        len(contrasts) != 4
        or {
            (contrast["anchor"], contrast["role"], contrast["new_partner"])
            for contrast in contrasts
        }
        != expected
    ):
        return False
    for contrast in contrasts:
        counts = (
            contrast["original_only"],
            contrast["replacement_only"],
            contrast["both_win"],
            contrast["both_lose"],
        )
        predicted = (3, 3, 0, 0) if contrast["role"] == "east" else (0, 0, 3, 3)
        if (
            contrast["complete_pairs"] != CASES
            or contrast["original_minus_replacement_bounds"] != ["0", "0"]
            or counts != predicted
        ):
            return False
    cells = diagnosis["matrix"]
    if len(cells) != 2 or any(len(row) != 2 for row in cells):
        return False
    return all(
        cell["observed"] == CASES
        and cell["outcomes"]
        == {"no_vote_pair": 0, "split_vote": 3, "same_unavailable": 0, "repaired": 3}
        and cell["receipt_before_vote"]
        == {"both": CASES, "east_only": 0, "west_only": 0, "neither": 0}
        for row in cells
        for cell in row
    )


async def _rehearse(manifest: Record, registration: Record) -> Record:
    pairs = _registered_pairs(registration)
    matrix = []
    observations: dict[str, Record | None] = {}
    decisions = worlds = 0
    for row in pairs:
        rates = []
        for cells in row:
            wins = 0
            for cell in cells:
                observation, count = await _episode(
                    cell, registration["teams"][cell["team"]]
                )
                observations[cell["ticket"]] = observation
                wins += observation["reward"]
                decisions += count
                worlds += 1
            rates.append(str(Fraction(wins, CASES)))
        matrix.append(rates)
    if worlds != 24 or decisions != 240:
        raise ValueError("native rehearsal did not execute all 240 private decisions")
    diagnosis = panel(
        manifest, registration, observations, source="scripted_native_worlds"
    )
    mixed = "1/2" if manifest["mode"] == "scripted-different" else "1"
    matched = (
        _offsetting_prediction(matrix, diagnosis)
        if manifest["mode"] == "scripted-offsetting"
        else matrix == [["1", mixed], [mixed, "1"]]
    )
    return {
        "schema": SCHEMA,
        "mode": manifest["mode"],
        "names": list(manifest["names"]),
        "registration_sha256": manifest["registration_sha256"],
        "matrix": matrix,
        "decisions": decisions,
        "worlds": worlds,
        "matched_prediction": matched,
        "diagnosis": diagnosis,
        "native_trials_started": 0,
        "model_calls": 0,
        "scope": SCOPE,
    }


def run(manifest: Record, registration: Record) -> Record:
    if manifest.get("mode") not in SCRIPTED_MODES:
        raise ValueError("native rehearsal supports scripted controls only")
    return asyncio.run(_rehearse(manifest, registration))
