import fcntl
import json
import os
import re
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from zipfile import BadZipFile, ZipFile

from ..peer_update.inspect_backend import run_inspect
from ..turn_fork.journal import _lock, _study
from ..visibility.clarify_run import (
    _DIRECTORY,
    StudyError,
    _absolute,
    _directory,
    _is_digest,
    _object,
    _private,
    _publish,
    _regular,
    _study_path,
)
from . import protocol

_SCHEMA = "seatlab.rolepack-study/v1"
_MAX_CONFIG = 64 * 1024
_MAX_ARCHIVE = 32 * 1024 * 1024
_MAX_HEADER = 1024 * 1024
_MAX_ROWS = 16_384
_MAX_DATASET = 32 * 1024 * 1024
_MAX_META = 256 * 1024
_LOG_NAME = re.compile(r"[A-Za-z0-9_.-]{1,240}\.eval")

type _Doc = dict[str, Any]


def _path(path: Path) -> Path:
    path = _absolute(path)
    if len(path.parts) > 128 or len(os.fsencode(path)) > 4096 or not path.name:
        raise StudyError("invalid_study_path")
    return path


def _bytes(directory: int, name: str, limit: int, mode: int | None = None) -> bytes:
    with _regular(directory, name, mode) as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise StudyError("study_file_too_large")
    return data


def _config(path: Path) -> bytes:
    try:
        with _directory(path.parent) as parent:
            return _bytes(parent, path.name, _MAX_CONFIG)
    except (OSError, ValueError):
        raise StudyError("invalid_dispatch_config_file") from None


def _read(directory: int, name: str) -> _Doc:
    try:
        return _object(_bytes(directory, name, _MAX_META, 0o600))
    except (ValueError, RecursionError):
        raise StudyError("invalid_study_record") from None


def _sealed(root: int, pack: _Doc) -> _Doc:
    doc = _read(root, "study.json")
    path = doc.get("config_path")
    if (
        set(doc) != {"schema", "pack_sha256", "config_sha256", "config_path"}
        or doc["schema"] != _SCHEMA
        or not _is_digest(doc["config_sha256"])
        or type(path) is not str
        or len(path) > 4096
        or not Path(path).is_absolute()
    ):
        raise StudyError("invalid_study_seal")
    if doc["pack_sha256"] != protocol.digest(pack):
        raise StudyError("study_pack_changed")
    return doc


def seal(study: Path, pack: _Doc, config: Path) -> _Doc:
    """Register a new private study and the bytes of a trusted dispatch config."""
    study = _study_path(_path(study))
    config = _path(config)
    pack = protocol.validate(pack)
    doc = {
        "schema": _SCHEMA,
        "pack_sha256": protocol.digest(pack),
        "config_sha256": sha256(_config(config)).hexdigest(),
        "config_path": str(config),
    }
    with _directory(study.parent) as parent:
        os.mkdir(study.name, 0o700, dir_fd=parent)
    with _study(study) as root, _lock(root, fcntl.LOCK_EX):
        _publish(root, "study.json", doc, mode=0o600)
    return {"schema": _SCHEMA, "pack_sha256": doc["pack_sha256"]}


def _batch_name(index: int) -> str:
    return f"batch-{index}"


def _exists(directory: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _claims(batch: int, first: int, rows: list[_Doc], digest: str) -> list[int]:
    doc = _read(batch, "batch.json")
    indices = doc.get("indices")
    if (
        set(doc) != {"pack_sha256", "indices"}
        or doc["pack_sha256"] != digest
        or type(indices) is not list
        or not 0 < len(indices) <= len(rows)
        or any(type(i) is not int or not first <= i < len(rows) for i in indices)
        or indices[0] != first
        or indices != sorted(set(indices))
    ):
        raise StudyError("invalid_batch_claims")
    for i in indices:
        row = rows[i]
        if row["prompt_sha256"] is None or _read(batch, f"claim-{i}.json") != row:
            raise StudyError("invalid_batch_claims")
    return indices


def _batches(root: int, rows: list[_Doc], digest: str) -> tuple[set[int], list[str]]:
    claimed: set[int] = set()
    batches = []
    for first in range(len(rows)):
        name = _batch_name(first)
        if not _exists(root, name):
            continue
        batch = os.open(name, _DIRECTORY, dir_fd=root)
        try:
            _private(batch, mode=0o700)
            indices = _claims(batch, first, rows, digest)
        finally:
            os.close(batch)
        if not claimed.isdisjoint(indices):
            raise StudyError("duplicate_batch_claims")
        claimed.update(indices)
        batches.append(name)
    return claimed, batches


def _state(root: int, pack: _Doc) -> tuple[_Doc, list[_Doc], set[int], list[str]]:
    seal_doc = _sealed(root, protocol.validate(pack))
    rows = protocol.plan(pack)
    if len(rows) > _MAX_ROWS:
        raise StudyError("too_many_study_rows")
    claimed, batches = _batches(root, rows, seal_doc["pack_sha256"])
    return seal_doc, rows, claimed, batches


def _samples(pack: _Doc, rows: list[_Doc], indices: list[int]) -> list[bytes]:
    by_id = {record["id"]: record for record in pack["records"]}
    dataset = []
    size = 0
    for i in indices:
        row = rows[i]
        text = protocol.prompt(by_id[row["record_id"]], row["arm"])
        if sha256(text.encode("utf-8")).hexdigest() != row["prompt_sha256"]:
            raise StudyError("study_prompt_changed")
        line = (
            json.dumps(
                {"id": row["sample_id"], "input": text},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        size += len(line)
        if size > _MAX_DATASET:
            raise StudyError("inspect_dataset_too_large")
        dataset.append(line)
    return dataset


def _reserve(
    study: Path, root: int, rows: list[_Doc], indices: list[int], digest: str
) -> Path:
    name = _batch_name(indices[0])
    with TemporaryDirectory(prefix=".stage-", dir=study) as staging:
        prepared = Path(staging) / "batch"
        prepared.mkdir(mode=0o700)
        with _directory(prepared) as batch:
            _publish(
                batch,
                "batch.json",
                {"pack_sha256": digest, "indices": indices},
                mode=0o600,
            )
            for i in indices:
                _publish(batch, f"claim-{i}.json", rows[i], mode=0o600)
        os.rename(prepared, name, dst_dir_fd=root)
    return study / name


def _log_data(log_dir: int, name: str) -> bytes:
    if type(name) is not str or _LOG_NAME.fullmatch(name) is None:
        raise StudyError("invalid_inspect_log_name")
    with _regular(log_dir, name) as stream:
        info = os.fstat(stream.fileno())
        if info.st_uid != os.geteuid() or info.st_nlink != 1:
            raise StudyError("invalid_inspect_log_file")
        raw = stream.read(_MAX_ARCHIVE + 1)
    if not raw.startswith(b"PK\x03\x04") or len(raw) > _MAX_ARCHIVE:
        raise StudyError("invalid_inspect_archive")
    return raw


def _verify_log(raw: bytes, ids: list[str]) -> None:
    try:
        with ZipFile(BytesIO(raw)) as archive:
            members = archive.infolist()
            headers = [entry for entry in members if entry.filename == "header.json"]
            if (
                len(members) > 4096
                or sum(entry.file_size for entry in members) > _MAX_ARCHIVE
                or len(headers) != 1
                or not 0 < headers[0].file_size <= _MAX_HEADER
            ):
                raise ValueError
            header = _object(archive.read(headers[0]))
        if (
            header.get("status") not in ("success", "error", "cancelled")
            or header.get("invalidated", False) is not False
            or header["eval"]["dataset"]["sample_ids"] != ids
        ):
            raise ValueError
    except (
        BadZipFile,
        OSError,
        RuntimeError,
        ValueError,
        TypeError,
        KeyError,
        RecursionError,
    ):
        raise StudyError("invalid_registered_inspect_archive") from None


def _write_input(path: Path, parts: list[bytes]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    with os.fdopen(os.open(path, flags, 0o600), "wb") as stream:
        stream.writelines(parts)


def _execute(batch: Path, config: bytes, samples: list[bytes], ids: list[str]) -> Path:
    log_dir = batch / "inspect"
    log_dir.mkdir(mode=0o700)
    try:
        with TemporaryDirectory(prefix=".input-", dir=batch) as scratch:
            dataset = Path(scratch) / "samples.jsonl"
            snapshot = Path(scratch) / "dispatch.json"
            _write_input(dataset, samples)
            _write_input(snapshot, [config])
            log = run_inspect(dataset, snapshot, log_dir)
    except (Exception, SystemExit):
        raise StudyError("inspect_run_failed") from None
    if not isinstance(log, Path) or log.parent != log_dir:
        raise StudyError("unregistered_inspect_log_location")
    with _directory(log_dir) as fd:
        _private(fd, mode=0o700)
        raw = _log_data(fd, log.name)
    _verify_log(raw, ids)
    with _study(batch.parent) as root, _lock(root, fcntl.LOCK_EX):
        with _directory(batch) as fd:
            _private(fd, mode=0o700)
            _publish(
                fd,
                "log.json",
                {"name": log.name, "sha256": sha256(raw).hexdigest()},
                mode=0o600,
            )
    return log


def run(study: Path, pack: _Doc, start: int, stop: int) -> _Doc:
    """Run one batch for untouched eligible rows in [start, stop).

    Skip existing claims. Once a batch is registered, all its indices remain
    claimed even if Inspect raises or stops before recording any sample.
    """
    study = _study_path(_path(study))
    with _study(study) as root, _lock(root, fcntl.LOCK_EX):
        doc, rows, claimed, _ = _state(root, pack)
        if (
            type(start) is not int
            or type(stop) is not int
            or not 0 <= start < stop <= len(rows)
        ):
            raise StudyError("range_outside_study")
        config = _config(_path(Path(doc["config_path"])))
        if sha256(config).hexdigest() != doc["config_sha256"]:
            raise StudyError("study_config_changed")
        eligible = [
            i for i in range(start, stop) if rows[i]["prompt_sha256"] is not None
        ]
        indices = [i for i in eligible if i not in claimed]
        job = None
        if indices:
            samples = _samples(pack, rows, indices)
            ids = [rows[i]["sample_id"] for i in indices]
            batch = _reserve(study, root, rows, indices, doc["pack_sha256"])
            job = (batch, config, samples, ids)
    log = _execute(*job) if job is not None else None
    return {
        "pack_sha256": doc["pack_sha256"],
        "start": start,
        "stop": stop,
        "claimed": len(indices),
        "already_claimed": len(eligible) - len(indices),
        "unavailable": stop - start - len(eligible),
        "inspect_log": str(log) if log is not None else None,
    }


def pending(study: Path, pack: _Doc) -> list[int]:
    """List only untouched eligible indices. The dispatch config is not needed offline."""
    with _study(_path(study)) as root, _lock(root, fcntl.LOCK_SH):
        _, rows, claimed, _ = _state(root, pack)
        return [
            i
            for i, row in enumerate(rows)
            if i not in claimed and row["prompt_sha256"] is not None
        ]


def snapshot(study: Path, pack: _Doc) -> tuple[list[Path], set[str]]:
    """Read pinned logs and every accepted claim at the same point in time."""
    study = _study_path(_path(study))
    found = []
    with _study(study) as root, _lock(root, fcntl.LOCK_SH):
        _, rows, claimed, batches = _state(root, pack)
        for name in batches:
            batch = study / name
            with _directory(batch) as fd:
                if not _exists(fd, "log.json"):
                    continue
                receipt = _read(fd, "log.json")
            if set(receipt) != {"name", "sha256"} or not _is_digest(receipt["sha256"]):
                raise StudyError("invalid_inspect_log_receipt")
            log_dir = batch / "inspect"
            with _directory(log_dir) as fd:
                _private(fd, mode=0o700)
                raw = _log_data(fd, receipt["name"])
            if sha256(raw).hexdigest() != receipt["sha256"]:
                raise StudyError("inspect_archive_changed")
            found.append(log_dir / receipt["name"])
    return found, {rows[index]["sample_id"] for index in claimed}
