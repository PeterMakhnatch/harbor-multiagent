from ...joint_reopen.observation import validate_observation
from ...native_evidence import Record, same
from . import plan, repeat_math

SCHEMA = "seatlab.partner-native-repeat/v1"
NATIVE = "collected_native_trials"
SCRIPTED = "scripted_native_worlds"
SCOPE = (
    "Finite descriptive contrasts on the same six registered calendars across two "
    "registrations. Bounds cover missing binary rewards only. Diagonal and mixed "
    "trials recur in different contrasts; do not pool them. Distinct registrations "
    "and physical worlds do not imply independent models or seeds. No population inference."
)
Layout = dict[tuple[str, int, str, str], str]
Grades = dict[tuple[str, int, str, str], int | None]


def _layout(manifest: Record, registration: Record) -> Layout:
    count = len(manifest["names"])
    teams = {plan._pair(i, j) for i in range(count) for j in range(count)}
    if set(registration["teams"]) != teams:
        raise ValueError("registered teams differ from the complete ordered grid")
    cells = registration["cells"]
    layout = {
        (cell["team"], cell["seed"], cell["fixture"], cell["channel"]): cell["ticket"]
        for cell in cells
    }
    if len(layout) != len(cells) or len(set(layout.values())) != len(cells):
        raise ValueError("repeated registered layout or ticket")
    seeds = {seed for team, seed, _, _ in layout if team == plan._pair(0, 0)}
    expected = {(team, seed, "both", "deliver") for team in teams for seed in seeds}
    if len(seeds) != plan.CASES or set(layout) != expected:
        raise ValueError("missing or mismatched registered case mapping")
    return layout


def _bind(
    manifest0: Record, registration0: Record, manifest1: Record, registration1: Record
) -> tuple[Layout, Layout]:
    for field in ("start", "names", "mode"):
        if not same(manifest0[field], manifest1[field]):
            raise ValueError(f"repeat differs in {field}")
    if not same(registration0["task"], registration1["task"]):
        raise ValueError("repeat differs in task identity")
    if not same(registration0["teams"], registration1["teams"]):
        raise ValueError("repeat differs in participant configuration")
    if manifest0["registration_sha256"] == manifest1["registration_sha256"]:
        raise ValueError("repeat requires distinct registration digests")
    layout0 = _layout(manifest0, registration0)
    layout1 = _layout(manifest1, registration1)
    if layout0.keys() != layout1.keys():
        raise ValueError("repeat differs in registered case layout")
    if set(layout0.values()).intersection(layout1.values()):
        raise ValueError("repeat registrations have overlapping tickets")
    return layout0, layout1


def compatible(
    manifest0: Record, registration0: Record, manifest1: Record, registration1: Record
) -> None:
    _bind(manifest0, registration0, manifest1, registration1)


def _grades(
    layout: Layout, observations: dict[str, Record | None], seen: set[str]
) -> Grades:
    if not isinstance(observations, dict) or set(observations) != set(layout.values()):
        raise ValueError("one observation slot is required for every registered trial")
    grades: Grades = {}
    for key, ticket in layout.items():
        observation = observations[ticket]
        grade: int | None = None
        if observation is not None:
            _, seed, fixture, channel = key
            checked = validate_observation(observation, seed, fixture, channel)
            run, reward = checked["run"], checked["reward"]
            assert isinstance(run, str) and type(reward) is int
            if run in seen:
                raise ValueError("one physical world cannot count for two trials")
            seen.add(run)
            grade = reward
        grades[key] = grade
    return grades


def _contrasts(names: list[str], grades0: Grades, grades1: Grades) -> list[Record]:
    seeds = sorted(seed for team, seed, _, _ in grades0 if team == plan._pair(0, 0))
    contrasts = []
    for i, anchor in enumerate(names):
        for j, partner in enumerate(names):
            if i == j:
                continue
            for role, mixed in (("east", plan._pair(i, j)), ("west", plan._pair(j, i))):
                quartets: list[repeat_math.Quartet] = []
                for seed in seeds:
                    home_key = (plan._pair(i, i), seed, "both", "deliver")
                    mixed_key = (mixed, seed, "both", "deliver")
                    quartets.append(
                        (
                            grades0[home_key],
                            grades1[home_key],
                            grades0[mixed_key],
                            grades1[mixed_key],
                        )
                    )
                contrasts.append(
                    {
                        "anchor": anchor,
                        "role": role,
                        "new_partner": partner,
                        "metrics": repeat_math.summarize(quartets),
                    }
                )
    return contrasts


def panel(
    manifest0: Record,
    registration0: Record,
    obs0: dict[str, Record | None],
    manifest1: Record,
    registration1: Record,
    obs1: dict[str, Record | None],
    *,
    source: str,
) -> Record:
    layout0, layout1 = _bind(manifest0, registration0, manifest1, registration1)
    if source not in (NATIVE, SCRIPTED) or (
        source == SCRIPTED and manifest0["mode"] not in plan.SCRIPTED_MODES
    ):
        raise ValueError(
            "evidence source does not apply to the registered partnerships"
        )
    seen: set[str] = set()
    grades0 = _grades(layout0, obs0, seen)
    grades1 = _grades(layout1, obs1, seen)
    registered = len(layout0) + len(layout1)
    names = manifest0["names"]
    return {
        "schema": SCHEMA,
        "source": source,
        "names": list(names),
        "registration_sha256": [
            manifest0["registration_sha256"],
            manifest1["registration_sha256"],
        ],
        "registered_trials": registered,
        "observed_trials": len(seen),
        "complete": len(seen) == registered,
        "contrasts": _contrasts(names, grades0, grades1),
        "scope": SCOPE,
    }
