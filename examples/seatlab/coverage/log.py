import fcntl
import os
import re
from collections.abc import Callable
from hashlib import sha256
from itertools import islice
from pathlib import Path
from typing import Any

from ..handoff.journal import _FAILED, _FINGERPRINT, _evidence, _received, _study
from ..handoff.packet import _IDENTIFIER
from ..visibility.clarify_run import _DIRECTORY, StudyError, _private, _publish
from ..visibility.route_audit import _block
from ..visibility.route_run import Answer
from . import jobs
from .jobs import Request

_CLAIM = "claim.json"
_RESPONSE = "response.json"
_CLAIM_SCHEMA = "seatlab.coverage-claim/v1"
_RESPONSE_SCHEMA = "seatlab.coverage-response/v1"
_ROLE = "route_decide"
_OWNER = re.compile(r"owner[0-7]")
_MAX_SLOTS = 512
_MAX_REQUESTS = _MAX_SLOTS * 10
_MISSING = {"status": "missing", "text": None, "response_sha256": None, "usage": None}


def _check_source(source_sha256: str) -> None:
    if type(source_sha256) is not str or _FINGERPRINT.fullmatch(source_sha256) is None:
        raise StudyError("invalid_source")


def _labels(values: tuple[str, ...], minimum: int, maximum: int) -> bool:
    return (
        type(values) is tuple
        and minimum <= len(values) <= maximum
        and all(type(value) is str and _IDENTIFIER.fullmatch(value) for value in values)
        and len(set(values)) == len(values)
    )


def _binding(source_sha256: str, request: Request) -> dict[str, Any]:
    if (
        type(request) is not Request
        or type(request.key) is not str
        or type(request.slot) is not int
        or not 0 <= request.slot < _MAX_SLOTS
        or type(request.policy) is not str
        or type(request.prompt) is not str
        or not request.prompt
        or not _labels(request.options, 2, 16)
    ):
        raise StudyError("invalid_request")
    if request.policy == "owners":
        suffix = request.key.rsplit("-", 1)[-1]
        if (
            _OWNER.fullmatch(suffix) is None
            or type(request.target) is not str
            or not _labels(request.ids, 1, 1)
            or request.ids != (request.target,)
        ):
            raise StudyError("invalid_request")
    elif request.policy in ("batch", "checklist"):
        suffix = request.policy
        if request.target is not None or not _labels(request.ids, 2, 8):
            raise StudyError("invalid_request")
    else:
        raise StudyError("invalid_request")
    if request.key != f"cv-{source_sha256[:12]}-{request.slot:03d}-{suffix}":
        raise StudyError("invalid_request")
    try:
        prompt_sha256 = sha256(request.prompt.encode("utf-8")).hexdigest()
    except UnicodeError:
        raise StudyError("invalid_request") from None
    return {
        "run": request.key,
        "role": _ROLE,
        "slot": request.slot,
        "policy": request.policy,
        "target": request.target,
        "source_sha256": source_sha256,
        "prompt_sha256": prompt_sha256,
        "ids": list(request.ids),
        "options": list(request.options),
    }


def _write(fd: int, name: str, document: dict[str, Any]) -> None:
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        _publish(fd, name, document, mode=0o600)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _verify(binding: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    status = receipt.get("status")
    if status == "callback_error":
        fields = _FAILED
    elif status == "received":
        text, usage = receipt.get("text"), receipt.get("usage")
        if type(text) is not str or (usage is not None and type(usage) is not dict):
            raise StudyError("invalid_evidence")
        try:
            fields = _received(Answer(text, **(usage or {})))
        except (TypeError, ValueError):
            raise StudyError("invalid_evidence") from None
    else:
        raise StudyError("invalid_evidence")
    if (
        type(receipt.get("slot")) is not int
        or receipt != binding | {"schema": _RESPONSE_SCHEMA} | fields
    ):
        raise StudyError("invalid_evidence")
    return fields


def _row(
    request: Request, binding: dict[str, Any], state: str, fields: dict[str, Any]
) -> dict[str, Any]:
    return {
        "run": request.key,
        "role": _ROLE,
        "slot": request.slot,
        "policy": request.policy,
        "target": request.target,
        "state": state,
        "status": fields["status"],
        "choices": jobs.answer(request, fields["text"])
        if fields["status"] == "received"
        else None,
        "prompt_sha256": binding["prompt_sha256"],
        "response_sha256": fields["response_sha256"],
        "usage": fields["usage"],
    }


def play(
    study: Path,
    frozen_sha256: str,
    request: Request,
    respond: Callable[[str, str, str], Answer],
) -> dict[str, Any]:
    _check_source(frozen_sha256)
    binding = _binding(frozen_sha256, request)
    try:
        with _study(study) as root:
            try:
                os.mkdir(request.key, 0o700, dir_fd=root)
            except FileExistsError:
                raise StudyError("block_already_claimed") from None
            fd = os.open(request.key, _DIRECTORY, dir_fd=root)
            try:
                _private(fd, mode=0o700)
                _write(fd, _CLAIM, binding | {"schema": _CLAIM_SCHEMA})
                interrupted = False
                try:
                    fields = _received(respond(request.prompt, request.key, _ROLE))
                except (Exception, SystemExit, KeyboardInterrupt) as error:  # noqa: BLE001 -- record the external callback's first failure
                    fields = _FAILED
                    interrupted = isinstance(error, KeyboardInterrupt)
                _write(fd, _RESPONSE, binding | {"schema": _RESPONSE_SCHEMA} | fields)
                if interrupted:
                    raise KeyboardInterrupt from None
            finally:
                os.close(fd)
    except OSError:
        raise StudyError("study_io_failed") from None
    return _row(request, binding, "claimed", fields)


def _snapshot(
    root: int, key: str, binding: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    with _block(root, key) as (fd, disk_state):
        if fd is None:
            if disk_state != "missing":
                raise StudyError("invalid_evidence")
            return "unclaimed", _MISSING
        fcntl.flock(fd, fcntl.LOCK_SH)
        with os.scandir(fd) as files:
            names = {file.name for file in islice(files, 3)}
        if not names:
            return "unbound", _MISSING
        if names - {_CLAIM, _RESPONSE}:
            raise StudyError("unexpected_evidence")
        if _CLAIM not in names:
            raise StudyError("invalid_evidence")
        claim = _evidence(fd, _CLAIM)
        if type(claim.get("slot")) is not int or claim != binding | {
            "schema": _CLAIM_SCHEMA
        }:
            raise StudyError("invalid_evidence")
        if _RESPONSE not in names:
            return "claimed", _MISSING
        return "claimed", _verify(binding, _evidence(fd, _RESPONSE))


def collect(
    study: Path, frozen_sha256: str, requests: tuple[Request, ...]
) -> list[dict[str, Any]]:
    _check_source(frozen_sha256)
    if type(requests) is not tuple or len(requests) > _MAX_REQUESTS:
        raise StudyError("invalid_register")
    bindings = tuple(_binding(frozen_sha256, request) for request in requests)
    keys = {request.key for request in requests}
    if len(keys) != len(requests):
        raise StudyError("invalid_register")
    try:
        with _study(study) as root:
            with os.scandir(root) as directories:
                if any(
                    item.name not in keys for item in islice(directories, len(keys) + 1)
                ):
                    raise StudyError("unexpected_evidence")
            return [
                _row(request, binding, *_snapshot(root, request.key, binding))
                for request, binding in zip(requests, bindings, strict=True)
            ]
    except OSError:
        raise StudyError("study_io_failed") from None
