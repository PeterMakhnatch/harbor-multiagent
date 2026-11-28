from pathlib import Path

from ...joint_reopen import collect
from ...joint_reopen.observation import validate_observation
from ...native_evidence import Record
from . import anchors, contact, results

SCHEMA = "seatlab.partner-native-diagnosis/v1"
NATIVE = "collected_native_trials"
SCRIPTED = "scripted_native_worlds"
SCOPE = (
    "Each contrast compares team outcomes on the same six registered calendars while one role's "
    "configuration stays fixed. The same trial appears in multiple contrasts; do not pool them. "
    "A returned inbox before a vote does not prove the role used its contents, or that the message "
    "was nonempty. The counts do not assign responsibility to a role. Bounds cover missing grades, "
    "not new trials or other models."
)


def panel(
    manifest: Record,
    registration: Record,
    observations: dict[str, Record | None],
    *,
    source: str,
) -> Record:
    if source not in (NATIVE, SCRIPTED) or (
        source == SCRIPTED and manifest["mode"] == "native-rosters"
    ):
        raise ValueError(
            "evidence source does not apply to the registered partnerships"
        )
    names = manifest["names"]
    labels = {
        f"p{i:02}-{j:02}": (i, j) for i in range(len(names)) for j in range(len(names))
    }
    cells = registration["cells"]
    if len(cells) != len(observations) or {cell["ticket"] for cell in cells} != set(
        observations
    ):
        raise ValueError("one observation slot is required for every registered trial")
    if set(registration["teams"]) != set(labels):
        raise ValueError("registered teams differ from the comparison")
    grades: dict[tuple[int, int], dict[int, int | None]] = {
        key: {} for key in labels.values()
    }
    evidence: dict[tuple[int, int], list[Record | None]] = {
        key: [] for key in labels.values()
    }
    seen_runs: set[str] = set()
    for cell in cells:
        seed = cell["seed"]
        key = labels[cell["team"]]
        if (
            cell["fixture"] != "both"
            or cell["channel"] != "deliver"
            or seed in grades[key]
        ):
            raise ValueError("unsupported or repeated native case")
        value = observations[cell["ticket"]]
        grade: int | None = None
        if value is not None:
            value = validate_observation(value, seed, "both", "deliver")
            run, reward = value["run"], value["reward"]
            assert isinstance(run, str) and type(reward) is int
            if run in seen_runs:
                raise ValueError("one physical world cannot count for two trials")
            seen_runs.add(run)
            grade = reward
        evidence[key].append(value)
        grades[key][seed] = grade
    return {
        "schema": SCHEMA,
        "source": source,
        "mode": manifest["mode"],
        "registration_sha256": manifest["registration_sha256"],
        "names": list(names),
        "registered_worlds": len(cells),
        "observed_worlds": len(seen_runs),
        "complete": len(seen_runs) == len(cells),
        "matrix": [
            [contact.summarize(evidence[i, j]) for j in range(len(names))]
            for i in range(len(names))
        ],
        "contrasts": anchors.contrasts(names, grades),
        "scope": SCOPE,
    }


def read_native(
    manifest: Record, registration: Record, roots: list[Path]
) -> dict[str, Record | None]:
    rows = collect.collect_registered(registration, roots)["cells"]
    names = range(len(manifest["names"]))
    pairs = [[f"p{i:02}-{j:02}" for j in names] for i in names]
    grouped = results._groups(pairs, registration, rows)
    return {
        row["ticket"]: row["observation"] if row["status"] == "usable" else None
        for group in grouped.values()
        for row in group
    }


def summarize(manifest: Record, registration: Record, roots: list[Path]) -> Record:
    return panel(
        manifest,
        registration,
        read_native(manifest, registration, roots),
        source=NATIVE,
    )
