import fcntl
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from hashlib import sha256
from itertools import islice
from pathlib import Path
from typing import Any, cast

from ..visibility import route_run
from ..visibility.clarify_run import (
    _DIRECTORY,
    StudyError,
    _directory,
    _encode,
    _is_digest,
    _object,
    _private,
    _publish,
    _regular,
    _study_path,
)
from . import protocol

type _Document = dict[str, Any]
type _Respond = Callable[[str, str, str], route_run.Answer]
type _Ask = Callable[[str, str], str | None]

_ROLES = {"push": "inventory", "decide": "dispatch"}
_FILES = {"claim.json", "push.json", "decide.json"}
_MAX_RECORD = 64 * 1024
_PUBLIC = (
    "stage",
    "role",
    "run",
    "prompt_sha256",
    "response_sha256",
    "usage",
    "status",
)
_FAILED = {
    "status": "callback_error",
    "text": None,
    "response_sha256": None,
    "usage": None,
}


def _claim(digest: str, source: _Document, arm: str) -> _Document:
    seed, rep = source.get("seed"), source.get("repetition")
    source_id, trial_id = source.get("source_id"), source.get("trial_id")
    if (
        not _is_digest(digest)
        or type(seed) is not int
        or not 6501 <= seed <= 6506
        or type(rep) is not int
        or rep not in (0, 1)
        or type(source_id) is not str
        or source_id != f"s{seed}-r{rep}"
        or type(arm) is not str
        or arm not in protocol.arms(seed, rep)
        or type(trial_id) is not str
        or not trial_id
    ):
        raise StudyError("invalid_cell")
    return {
        "schema": "seatlab.turn-fork-claim/v1",
        "source_id": source_id,
        "seed": seed,
        "repetition": rep,
        "arm": arm,
        "packet_sha256": digest,
        "source_sha256": sha256(_encode(source)).hexdigest(),
        "trial_id": trial_id,
    }


def _name(claim: _Document) -> str:
    return f"{claim['source_id']}-{claim['arm']}"


@contextmanager
def _study(study: Path) -> Iterator[int]:
    try:
        with _directory(_study_path(study)) as root:
            _private(root, mode=0o700)
            yield root
    except OSError:
        raise StudyError("study_io_failed") from None


@contextmanager
def _lock(root: int, mode: int) -> Iterator[None]:
    fcntl.flock(root, mode)
    try:
        yield
    finally:
        fcntl.flock(root, fcntl.LOCK_UN)


def _start(root: int, claim: _Document) -> int:
    name = _name(claim)
    with _lock(root, fcntl.LOCK_EX):
        try:
            os.mkdir(name, 0o700, dir_fd=root)
        except FileExistsError:
            raise StudyError("cell_already_claimed") from None
        fd = os.open(name, _DIRECTORY, dir_fd=root)
        try:
            _private(fd, mode=0o700)
            _publish(fd, "claim.json", claim, mode=0o600)
        except BaseException:
            os.close(fd)
            raise
    return fd


def _header(claim: _Document, stage: str, prompt: str) -> dict[str, str]:
    return {
        "schema": "seatlab.turn-fork-turn/v1",
        "claim_sha256": sha256(_encode(claim)).hexdigest(),
        "stage": stage,
        "role": _ROLES[stage],
        "run": protocol.run_id(
            claim["packet_sha256"], claim["source_id"], claim["arm"], stage
        ),
        "prompt_sha256": sha256(prompt.encode("utf-8")).hexdigest(),
    }


def _received(answer: route_run.Answer) -> _Document:
    raw, usage = route_run._validate_answer(answer)
    return {
        "status": "received",
        "text": answer.text,
        "response_sha256": sha256(raw).hexdigest(),
        "usage": usage,
    }


def _episode(source: _Document, arm: str, ask: _Ask) -> _Document | None:
    text = ask("push", protocol.push_prompt(source, arm))
    if text is None:
        return None
    try:
        opened = protocol.parse_push(text)
    except ValueError:
        return None
    text = ask("decide", protocol.decide_prompt(source, opened))
    if text is None:
        return None
    try:
        return protocol.grade(source, opened, text)
    except ValueError:
        return None


def _row(
    claim: _Document,
    turns: list[_Document],
    outcome: _Document | None,
    *,
    claimed: bool,
) -> _Document:
    state = (
        "unclaimed" if not claimed else "incomplete" if outcome is None else "complete"
    )
    return {
        **{key: claim[key] for key in ("source_id", "seed", "repetition", "arm")},
        "state": state,
        "outcome": outcome,
        "turns": turns,
    }


def _public(receipt: _Document) -> _Document:
    return {key: receipt[key] for key in _PUBLIC}


def play(
    study: Path, digest: str, source: _Document, arm: str, respond: _Respond
) -> _Document:
    claim = _claim(digest, source, arm)
    turns: list[_Document] = []
    with _study(study) as root:
        fd = _start(root, claim)
        try:

            def ask(stage: str, prompt: str) -> str | None:
                header = _header(claim, stage, prompt)
                interrupted = False
                try:
                    fields = _received(respond(prompt, header["run"], header["role"]))
                except (Exception, SystemExit, KeyboardInterrupt) as error:
                    fields = _FAILED
                    interrupted = isinstance(error, KeyboardInterrupt)
                receipt = header | fields
                with _lock(root, fcntl.LOCK_EX):
                    _publish(fd, f"{stage}.json", receipt, mode=0o600)
                if interrupted:
                    raise KeyboardInterrupt from None
                turns.append(_public(receipt))
                return cast(str | None, fields["text"])

            outcome = _episode(source, arm, ask)
        finally:
            os.close(fd)
    return _row(claim, turns, outcome, claimed=True)


def _read(fd: int, name: str) -> _Document:
    try:
        with _regular(fd, name, 0o600) as stream:
            raw = stream.read(_MAX_RECORD + 1)
        if len(raw) > _MAX_RECORD:
            raise ValueError("oversized record")
        return _object(raw)
    except (OSError, ValueError, RecursionError):
        raise StudyError("invalid_evidence") from None


def _verify(header: dict[str, str], receipt: _Document) -> str | None:
    if receipt.get("status") == "callback_error":
        fields = _FAILED
    else:
        text, usage = receipt.get("text"), receipt.get("usage")
        if type(text) is not str or (usage is not None and not isinstance(usage, dict)):
            raise StudyError("invalid_evidence")
        try:
            fields = _received(route_run.Answer(text, **(usage or {})))
        except (TypeError, ValueError):
            raise StudyError("invalid_evidence") from None
    if receipt != (header | fields):
        raise StudyError("invalid_evidence")
    return cast(str | None, fields["text"])


def _collect(fd: int, claim: _Document, source: _Document) -> _Document:
    _private(fd, mode=0o700)
    with os.scandir(fd) as entries:
        remaining = {entry.name for entry in islice(entries, len(_FILES) + 1)}
    if not remaining <= _FILES or "claim.json" not in remaining:
        raise StudyError("invalid_evidence")
    if _encode(_read(fd, "claim.json")) != _encode(claim):
        raise StudyError("invalid_evidence")
    remaining.remove("claim.json")
    turns: list[_Document] = []

    def ask(stage: str, prompt: str) -> str | None:
        name = f"{stage}.json"
        if name not in remaining:
            return None
        receipt = _read(fd, name)
        text = _verify(_header(claim, stage, prompt), receipt)
        remaining.remove(name)
        turns.append(_public(receipt))
        return text

    outcome = _episode(source, claim["arm"], ask)
    if remaining:
        raise StudyError("invalid_evidence")
    return _row(claim, turns, outcome, claimed=True)


def collect(study: Path, digest: str, source: _Document, arm: str) -> _Document:
    claim = _claim(digest, source, arm)
    with _study(study) as root, _lock(root, fcntl.LOCK_SH):
        try:
            fd = os.open(_name(claim), _DIRECTORY, dir_fd=root)
        except FileNotFoundError:
            return _row(claim, [], None, claimed=False)
        try:
            return _collect(fd, claim, source)
        finally:
            os.close(fd)
