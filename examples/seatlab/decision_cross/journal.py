import fcntl
import os
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..turn_fork import journal as native
from ..turn_fork import packet, protocol
from ..visibility import route_run
from ..visibility.clarify_run import StudyError, _encode, _is_digest, _publish
from . import inputs

type _Record = dict[str, Any]
type _Respond = Callable[[str, str, str], route_run.Answer]

_DRAW = ("index", "source_id", "obs_id", "prompt_sha256", "draw")
_OBS = ("source_id", "obs_id", "prompt_sha256", "opened")
_PUBLIC = ("status", "prompt_sha256", "response_sha256", "usage")


def _prepare(
    digest: str, draw: _Record, source: _Record, obs: _Record
) -> tuple[_Record, str, tuple[str, str]]:
    try:
        source_id, _ = packet._source(source)
        draw = packet._object(draw, *_DRAW)
        obs = packet._object(obs, *_OBS)
        index, number, aliases = draw["index"], draw["draw"], obs["opened"]
        if (
            not _is_digest(digest)
            or type(index) is not int
            or not 0 <= index < 96
            or type(number) is not int
            or number not in (0, 1)
            or type(aliases) is not list
            or len(aliases) != 2
        ):
            raise ValueError("invalid draw")
        opened = (aliases[0], aliases[1])
        prompt = protocol.decide_prompt(source, opened)
        prompt_digest = sha256(prompt.encode("utf-8")).hexdigest()
        obs_id = f"{source_id}-{prompt_digest[:12]}"
        identity = {
            "source_id": source_id,
            "obs_id": obs_id,
            "prompt_sha256": prompt_digest,
        }
        if any(
            draw[key] != value or obs[key] != value for key, value in identity.items()
        ):
            raise ValueError("invalid observation")
        claim = {
            "schema": "seatlab.decision-cross-claim/v1",
            "board_sha256": digest,
            "source_sha256": sha256(_encode(source)).hexdigest(),
            "draw": {"index": index, **identity, "draw": number},
            "opened": list(opened),
            "run": inputs.run_id(digest, obs_id, number),
        }
    except (ValueError, TypeError, RecursionError):
        raise StudyError("invalid_cell") from None
    return claim, prompt, opened


def _names(claim: _Record) -> tuple[str, str]:
    draw = claim["draw"]
    stem = f"{draw['obs_id']}-r{draw['draw']}"
    return f"{stem}.claim.json", f"{stem}.turn.json"


def _exists(root: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=root, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _header(claim: _Record) -> dict[str, str]:
    return {
        "schema": "seatlab.decision-cross-turn/v1",
        "claim_sha256": sha256(_encode(claim)).hexdigest(),
        "prompt_sha256": claim["draw"]["prompt_sha256"],
        "run": claim["run"],
        "role": "dispatch",
    }


def _row(
    claim: _Record,
    source: _Record,
    opened: tuple[str, str],
    receipt: _Record | None,
    *,
    claimed: bool,
) -> _Record:
    turn = outcome = None
    if receipt is not None:
        text = native._verify(_header(claim), receipt)
        turn = {key: receipt[key] for key in _PUBLIC}
        if text is not None:
            try:
                outcome = protocol.grade(source, opened, text)
            except ValueError:
                pass
    state = (
        "unclaimed" if not claimed else "incomplete" if outcome is None else "complete"
    )
    return {
        **claim["draw"],
        "run": claim["run"],
        "state": state,
        "turn": turn,
        "outcome": outcome,
    }


def collect(
    study: Path, digest: str, draw: _Record, source: _Record, obs: _Record
) -> _Record:
    claim, _, opened = _prepare(digest, draw, source, obs)
    claim_name, turn_name = _names(claim)
    with native._study(study) as root, native._lock(root, fcntl.LOCK_SH):
        claimed = _exists(root, claim_name)
        has_turn = _exists(root, turn_name)
        if (claimed and _encode(native._read(root, claim_name)) != _encode(claim)) or (
            not claimed and has_turn
        ):
            raise StudyError("invalid_evidence")
        receipt = native._read(root, turn_name) if has_turn else None
    return _row(claim, source, opened, receipt, claimed=claimed)


def play(
    study: Path,
    digest: str,
    draw: _Record,
    source: _Record,
    obs: _Record,
    respond: _Respond,
) -> _Record:
    claim, prompt, opened = _prepare(digest, draw, source, obs)
    claim_name, turn_name = _names(claim)
    with native._study(study) as root:
        with native._lock(root, fcntl.LOCK_EX):
            if _exists(root, claim_name):
                raise StudyError("cell_already_claimed")
            if _exists(root, turn_name):
                raise StudyError("invalid_evidence")
            _publish(root, claim_name, claim, mode=0o600)
        interrupted = False
        try:
            fields = native._received(respond(prompt, claim["run"], "dispatch"))
        except (Exception, SystemExit, KeyboardInterrupt) as error:
            fields = native._FAILED
            interrupted = isinstance(error, KeyboardInterrupt)
        receipt = _header(claim) | fields
        with native._lock(root, fcntl.LOCK_EX):
            _publish(root, turn_name, receipt, mode=0o600)
        if interrupted:
            raise KeyboardInterrupt from None
    return _row(claim, source, opened, receipt, claimed=True)
