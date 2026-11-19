import hashlib
import logging
import os
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel

from harbor.models.trial.config import ServiceVolumeConfig

_COMPOSE_PROJECT_PREFIX = "hb-"
_COMPOSE_PROJECT_HASH_LEN = 12


def compose_project_name(session_id: str) -> str:
    """Return the Docker Compose project name for an environment session.

    Compose puts the project name into every container name
    (``<project>-<service>-1``), network name (``<project>_<network>``) and
    named volume name. Processes in the environment can read these: Docker's
    DNS answers reverse lookups with the container and network names, and a
    named volume's host path is in ``/proc/self/mountinfo``. A session id starts
    with the trial name, which starts with the task name, and a task name can
    carry a condition, a role or a hint. So the project name is a hash of the
    session id, not the session id itself.

    The name is the same for one session id in every process, so start, exec,
    stop and cleanup always find the same project. It is a valid Compose project
    name and a valid DNS label.
    """
    digest = hashlib.sha256(session_id.encode()).hexdigest()
    return f"{_COMPOSE_PROJECT_PREFIX}{digest[:_COMPOSE_PROJECT_HASH_LEN]}"


class ComposeInfraEnvVars(BaseModel):
    """Docker Compose template infrastructure environment variables."""

    main_image_name: str
    context_dir: str
    prebuilt_image_name: str | None = None
    egress_control_sidecar_image_name: str | None = None
    egress_control_initial_network_mode: str | None = None
    egress_control_initial_allowed_hosts: str | None = None
    cpus: int | float | None = None
    memory: str | None = None

    def to_env_dict(self, include_os_env: bool = False) -> dict[str, str]:
        env_dict = os.environ.copy() if include_os_env else {}
        for field_name, value in self.model_dump(exclude_none=True).items():
            env_dict[field_name.upper()] = str(value)
        return env_dict


def merge_compose_env(
    *,
    base_env: Mapping[str, str] | None = None,
    user_env: Mapping[str, str] | None = None,
    infra_env: Mapping[str, str],
    logger: logging.Logger,
    collision_label: str = "Task/persistent env vars",
) -> dict[str, str]:
    """Merge compose env vars with Harbor infra vars taking precedence."""
    env_vars = dict(base_env or {})
    user_vars = dict(user_env or {})

    collisions = sorted(set(user_vars) & set(infra_env))
    if collisions:
        logger.warning(
            "%s are reserved by Harbor compose infra and will be ignored: %s",
            collision_label,
            ", ".join(collisions),
        )

    env_vars.update(user_vars)
    env_vars.update(infra_env)
    return env_vars


_LEGACY_LOG_MOUNT_SUFFIXES = {
    "verifier": "VERIFIER_LOGS",
    "agent": "AGENT_LOGS",
    "artifacts": "ARTIFACTS",
}


def legacy_log_mount_env_vars(
    mounts: list[ServiceVolumeConfig],
    *,
    host_value: Literal["source", "target"],
) -> dict[str, str]:
    """Compose env vars kept for old task-authored log mounts."""
    env_vars: dict[str, str] = {}
    for mount in mounts:
        target = mount.get("target")
        if mount.get("type") != "bind" or not target:
            continue

        suffix = target.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        legacy_name = _LEGACY_LOG_MOUNT_SUFFIXES.get(suffix)
        if legacy_name is None:
            continue

        env_vars[f"ENV_{legacy_name}_PATH"] = target
        env_vars[f"HOST_{legacy_name}_PATH"] = mount[host_value]
    return env_vars
