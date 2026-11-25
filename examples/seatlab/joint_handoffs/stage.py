import hashlib
import tempfile
from pathlib import Path
from typing import Any

from ...native_study import require
from ...seat_checkpoints.materialize import read_plain, sync_directory, target
from ...ship_inflight.materialize import publish
from ..physical_dispatch.stage import (
    BASE,
    BASE_FILES,
    STOCK_FILES,
    File,
    fill,
    stock,
)
from ..physical_dispatch.stage import (
    verify as verify_physical_tree,
)

PACKAGE_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = PACKAGE_DIR.parent.parent
TASK_NAME = "harbor/exhibit-relay-joint"
SCHEMA = "harbor.exhibit-relay-joint.materialized/v1"
DOMAIN = b"exhibit-relay-joint/v1\n"
ADDED = "environment/exhibit_relay/joint_cases.py"
SOURCES = (("README.md", "TASK-GUIDE.md"), (ADDED, "world.py"))
REVISIONS = (
    ("task.toml", 'name = "harbor/exhibit-relay"\n', f'name = "{TASK_NAME}"\n'),
    (
        "task.toml",
        'description = "Coordinate a museum exhibit dispatch using limited records across three private roles."\n',
        'description = "Coordinate a museum exhibit dispatch across three private roles under controlled record maps and delivery channels."\n',
    ),
    (
        "task.toml",
        'EXHIBIT_LAYOUT = "draw"\n',
        (
            'EXHIBIT_LAYOUT = "draw"\n'
            'EXHIBIT_REGISTRY_MAP = "original"\n'
            'EXHIBIT_BAY_MAP = "original"\n'
            'EXHIBIT_CHANNELS = "both"\n'
        ),
    ),
    (
        "environment/docker-compose.yaml",
        '      EXHIBIT_LAYOUT: "${EXHIBIT_LAYOUT:-draw}"\n',
        (
            '      EXHIBIT_LAYOUT: "${EXHIBIT_LAYOUT:-draw}"\n'
            '      EXHIBIT_REGISTRY_MAP: "${EXHIBIT_REGISTRY_MAP:-original}"\n'
            '      EXHIBIT_BAY_MAP: "${EXHIBIT_BAY_MAP:-original}"\n'
            '      EXHIBIT_CHANNELS: "${EXHIBIT_CHANNELS:-both}"\n'
        ),
    ),
    (
        "environment/serve.py",
        "from exhibit_relay.engine import Session\n",
        (
            "from exhibit_relay.engine import Session\n"
            "from exhibit_relay.joint_cases import CHANNELS, MAPS, delivery, select\n"
        ),
    ),
    (
        "environment/serve.py",
        "    def __init__(self, root: Path, control: Path, session: Session) -> None:\n",
        (
            "    def __init__(\n"
            "        self, root: Path, control: Path, session: Session, channels: str\n"
            "    ) -> None:\n"
            "        if channels not in CHANNELS:\n"
            '            raise ValueError("Invalid delivery channels")\n'
        ),
    ),
    (
        "environment/serve.py",
        "        self.session = session\n",
        "        self.session = session\n        self.channels = channels\n",
    ),
    (
        "environment/serve.py",
        "                return self.session.view(role)\n",
        "                return delivery(self.session.view(role), self.channels)\n",
    ),
    (
        "environment/serve.py",
        "            return self.session.apply(role, request)\n",
        "            return delivery(self.session.apply(role, request), self.channels)\n",
    ),
    (
        "environment/serve.py",
        "    session = Session(make_case(seed, profile, layout=layout), mode)\n",
        (
            '    registry_map = os.environ.get("EXHIBIT_REGISTRY_MAP", "original")\n'
            '    bay_map = os.environ.get("EXHIBIT_BAY_MAP", "original")\n'
            '    channels = os.environ.get("EXHIBIT_CHANNELS", "both")\n'
            '    if (profile, mode, layout) != ("fork", "linked", "draw"):\n'
            '        raise ValueError("Joint dispatch requires fork/linked/draw")\n'
            "    if registry_map not in MAPS or bay_map not in MAPS:\n"
            '        raise ValueError("Invalid joint record map")\n'
            "    if channels not in CHANNELS:\n"
            '        raise ValueError("Invalid delivery channels")\n'
            "    case = make_case(seed, profile, layout=layout)\n"
            "    session = Session(select(case, registry_map, bay_map), mode)\n"
        ),
    ),
    (
        "environment/serve.py",
        '    with Family(Path("/run/relay"), Path("/run/relay-control"), session):\n',
        (
            "    with Family(\n"
            '        Path("/run/relay"), Path("/run/relay-control"), session, channels\n'
            "    ):\n"
        ),
    ),
    (
        "solution/policies.py",
        "        if bay is not None:\n            routes[exhibit] = bay\n",
        '        if bay in view["bays"]:\n            routes[exhibit] = bay\n',
    ),
    (
        "instructions/loader.md",
        "Records may be incomplete or absent. ",
        (
            "Records may be incomplete or absent. "
            "A delivered record whose `value` consists only of `?` characters has a withheld value; treat that value as unknown. "
        ),
    ),
)


def expected() -> tuple[dict[str, int], dict[str, File]]:
    directories, files = stock()
    directories[""] = 0o700
    for relative, old, new in REVISIONS:
        data, mode = files[relative]
        text = data.decode("utf-8")
        require(text.count(old) == 1, f"stock revision differs: {relative}")
        files[relative] = (text.replace(old, new).encode("utf-8"), mode)
    for relative, source in SOURCES:
        data, _ = read_plain(PACKAGE_DIR / source)
        require(
            bool(data.decode("utf-8").strip()) and data.endswith(b"\n"),
            f"invalid {source}",
        )
        mode = files[relative][1] if relative in files else 0o644
        files[relative] = (data, mode)
    require(set(files) == {*STOCK_FILES, ADDED}, "joint files differ")
    return directories, files


def verify(root: Path, directories: dict[str, int], files: dict[str, File]) -> str:
    tree = verify_physical_tree(root, directories, files)
    return "sha256:" + hashlib.sha256(DOMAIN + tree.encode("ascii")).hexdigest()


def check_stage(task: Path) -> str:
    return verify(task.expanduser().absolute(), *expected())


def materialize(out: Path) -> dict[str, Any]:
    out = target(str(out))
    require(
        not out.is_relative_to(EXAMPLES_DIR.parent), "OUT must be outside the checkout"
    )
    directories, files = expected()
    with tempfile.TemporaryDirectory(
        prefix=".exhibit-joint-stage-", dir=out.parent
    ) as temporary:
        root = Path(temporary) / "task"
        root.mkdir(mode=0o700)
        fill(root, directories, files, [], [])
        stage_digest = verify(root, directories, files)
        publish(root, out)
    sync_directory(out.parent)
    return {
        "schema": SCHEMA,
        "task": TASK_NAME,
        "path": str(out),
        "base": dict(BASE),
        "base_files": BASE_FILES,
        "stage_digest": stage_digest,
        "files": {
            relative: "sha256:" + hashlib.sha256(data).hexdigest()
            for relative, (data, _) in sorted(files.items())
        },
    }
