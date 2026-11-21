import logging
from pathlib import Path

from harbor.agents.base import BaseAgent

from . import confirmation
from .agent import DECLARED_MODEL, AttentionSeat

_CELLS = {cell["cell_id"]: cell for cell in confirmation.grid()}


class ConfirmationSeat(AttentionSeat):
    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        cell_id: str,
        transport: str,
        command_config: str | None = None,
        logger: logging.Logger | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        if not isinstance(cell_id, str) or cell_id not in _CELLS:
            raise ValueError("Confirmation seat requires a registered cell id")
        if (
            transport != "direct"
            or model_name != DECLARED_MODEL
            or command_config is not None
            or extra_env
        ):
            raise ValueError("Confirmation seat requires the registered direct model")
        BaseAgent.__init__(self, logs_dir=logs_dir, model_name=model_name, logger=logger)
        self._cell = _CELLS[cell_id]
        self._command_config = None
        self._command = None
        self._started = False
