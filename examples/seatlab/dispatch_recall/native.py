from pathlib import Path
from runpy import run_path

ENVIRONMENT = (
    Path(__file__).resolve().parents[2] / "tasks" / "dispatch-recall" / "environment"
)
_world = run_path(str(ENVIRONMENT / "recall_world.py"))
Session = _world["Session"]
layout = _world["layout"]
diagnose = run_path(str(ENVIRONMENT / "recall_evidence.py"))["diagnose"]
encode_reply = run_path(str(ENVIRONMENT / "recall_server.py"))["encode_reply"]
