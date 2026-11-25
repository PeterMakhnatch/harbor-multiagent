from fractions import Fraction
from itertools import product
from typing import Any

from ...native_study import require
from ..decision_boundary.core import World, analyze
from ..decision_boundary.pair import compare
from ..exhibit_wire import encode_request, render_turn, request_stamp
from . import certificate as native
from . import policies
from .stage_labels import _current, summarize_versions

Json = dict[str, Any]
SCHEMA = "seatlab.physical-dispatch.freshness/v1"
SEARCH_LIMIT = 256
MAPS = ("original", "swapped")
TREATMENTS = ("current", "expired", "unrelated")
POLICY = {"current": "relevant", "unrelated": "other"}
DEPOT_CLASS = {"current": "current", "expired": "expired", "unrelated": "other_key"}
SUPPORT = {"current": 2, "expired": 0, "unrelated": 0}
CLASSES = {"current": 2, "expired": 1, "unrelated": 1}
OPTIMUM = {"current": "1", "expired": "1/2", "unrelated": "1/2"}
REJECTIONS = ("non_focal", "no_expired_record", "expired_inside_focal_bays")
LABELS = ("on_depot_agenda", "registrar", "depot")
COMPARISONS = {
    ("unrelated", "current"): ("refinement", "1/2"),
    ("expired", "current"): ("refinement", "1/2"),
    ("expired", "unrelated"): ("equivalent", "0"),
}
SCOPE = (
    "Conditional development certificate only: one public stock fork draw that is "
    "naturally focal and expired-eligible, two equally weighted bay maps, fixed "
    "view-only policies and exhaustive native grading. It shows what each final "
    "loader packet can support; it makes no claim about model effects, does not "
    "assert that unrelated and expired requests are equal, does not use or replace "
    "enrolled study cases, and a destination matching a stale chain is not proof "
    "that an agent followed that chain."
)


def _upstream(case: Json) -> tuple[list[Json], Json]:
    session = native.Session(case, "linked")
    initial = policies.nominate(session.view("loader"))
    session.apply("loader", initial)
    registration = policies.registrar(session.view("registrar"))
    session.apply("registrar", registration)
    view = session.view("depot")
    sizes = (len(initial["slips"]), len(registration["ids"]), len(view["agenda"]))
    require(sizes == (1, 2, 2), "upstream selection or depot agenda sizes differ")
    return [initial, registration], view


def expired_eligibility(view: Json) -> tuple[list[str] | None, str | None]:
    turn = (view.get("role"), view.get("phase"), view.get("turn"))
    require(turn == ("depot", "depot", "depot"), "expected depot in depot phase")
    agenda, cards = view["agenda"], view["cards"]
    keys = sorted({entry["key"] for entry in agenda})
    epochs = {entry["epoch"] for entry in agenda}
    require(len(agenda) == len(keys) == 2, "expected two distinct agenda crates")
    require(len(epochs) == 1, "agenda crates differ in epoch")
    require(8 <= len(cards) <= 24, "invalid depot archive size")
    epoch = epochs.pop()
    focal_bays = {_current(cards, key, epoch)["value"] for key in keys}
    require(len(focal_bays) == 2, "agenda crates share a current bay")
    chosen = []
    for key in keys:
        expired = [card for card in cards if card["key"] == key and card["end"] < epoch]
        eligible = [card["id"] for card in expired if card["value"] not in focal_bays]
        if not eligible:
            return None, "expired_inside_focal_bays" if expired else "no_expired_record"
        chosen.append(min(eligible))
    require(len(set(chosen)) == 2, "duplicate expired filing record")
    return sorted(chosen), None


def stale_commit(view: Json) -> Json:
    order = view["order"]
    routes: dict[str, str] = {}
    turn = (view["phase"], view["turn"], len(order["items"]))
    require(turn == ("dispatch", "loader", 2), "not a two-item dispatch turn")
    for item in order["items"]:
        crate = _current(view["registrar_file"], item, order["epoch"])["value"]
        chain = [card for card in view["depot_file"] if card["key"] == crate]
        require(len(chain) == 1 and chain[0]["end"] < order["epoch"], "no stale chain")
        routes[item] = chain[0]["value"]
    require(set(routes.values()) <= set(view["bays"]), "stale chain leaves the menu")
    return {"op": "commit", "routes": dict(sorted(routes.items()))}


def _search(start: int) -> tuple[int, int, Json, dict[str, int]]:
    require(type(start) is int and 0 <= start < 2**63, "invalid development seed")
    rejections = dict.fromkeys(REJECTIONS, 0)
    for seed in range(start, min(start + SEARCH_LIMIT, 2**63)):
        case = native.make_case(seed, "fork", layout="draw")
        focal = case["realized"] == min(brief["id"] for brief in case["briefs"])
        reason = expired_eligibility(_upstream(case)[1])[1] if focal else "non_focal"
        if reason is None:
            checked = seed - start + 1
            require(sum(rejections.values()) == checked - 1, "search counts disagree")
            return seed, checked, case, rejections
        rejections[reason] += 1
    raise ValueError("no focal expired-eligible draw within bounded development search")


def _maps(base: Json, instruction: str) -> dict[str, Json]:
    selected, packets = native.case_maps(base), set()
    common = {key: value for key, value in base.items() if key != "depot"}
    for case in selected.values():
        own = {key: value for key, value in case.items() if key != "depot"}
        require(own == common, "private map changed a common native case field")
        view = native.cli_view(native.Session(case, "linked").view("loader"))
        packets.add(encode_request(render_turn(instruction, view, None)))
    require(len(packets) == 1, "initial loader inputs differ across maps")
    return selected


def _dispatch(case: Json, prefix: list[Json]) -> tuple[Json, list[Json], Json]:
    session = native.Session(case, "linked")
    initial, registration, filing = prefix
    session.apply("loader", initial)
    session.apply("registrar", registration)
    agenda = session.view("depot")["agenda"]
    session.apply("depot", filing)
    view = session.view("loader")
    require(view["phase"] == "dispatch", "native dispatch phase differs")
    return session.snapshot(), agenda, native.cli_view(view)


def _check_filing(case: Json, view: Json, agenda: list[Json], arm: str) -> None:
    order, filed = view["order"], view["depot_file"]
    epoch, keys = order["epoch"], {entry["key"] for entry in agenda}
    focal = min(brief["id"] for brief in case["briefs"])
    require(order["id"] == focal == case["realized"], "realized order is not focal")
    require({entry["epoch"] for entry in agenda} == {epoch}, "agenda epoch differs")
    if arm != "expired":
        return
    bays = {_current(case["depot"], key, epoch)["value"] for key in keys}
    require(
        sorted(card["key"] for card in filed) == sorted(keys)
        and all(card["end"] < epoch and card["value"] not in bays for card in filed),
        "expired filing is not a strictly prior out-of-focus chain",
    )


def _label(action: Json, items: list[str], bays: list[str]) -> str:
    routes = action["routes"]
    require(sorted(routes) == items, "commit routes differ from the order")
    return f"{bays.index(routes[items[0]])}:{bays.index(routes[items[1]])}"


def _expired_grade(cut: Json) -> Json:
    result = native.grade(cut)
    reward = result["reward"]
    require(result["status"] == "complete", "native verifier did not complete")
    require(type(reward) is int and reward in (0, 1), "invalid native physical reward")
    require(reward == int(result["correct_items"] == 2), "route count, reward differ")
    roles = ("registrar", "depot", "chains", "dispatch")
    support = tuple(result[f"{role}_supported"] for role in roles)
    require(support == (2, 0, 0, 0), "native support differs from the expired filing")
    return result


def _labels(cut: Json, arm: str) -> tuple[list[Json], Json]:
    labels = summarize_versions(cut)
    registrar, depot = labels["registrar"], labels["depot"]
    counts = (labels["on_depot_agenda"], registrar["current"], depot[DEPOT_CLASS[arm]])
    totals = (sum(registrar.values()), sum(depot.values()))
    require(counts == (2, 2, 2) and totals == (2, 2), "stage labels differ from arm")
    return labels["items"], {key: labels[key] for key in LABELS}


def _row(case: Json, arm: str, name: str, instruction: str) -> Json:
    published = None
    if arm == "expired":
        upstream, depot = _upstream(case)
        ids, reason = expired_eligibility(depot)
        require(
            reason is None and ids is not None and depot["quota"] >= 2,
            "no eligible stale filing",
        )
        prefix = [*upstream, {"op": "file", "ids": ids}]
    else:
        published = native._enumerate(case, POLICY[arm], name, instruction)
        prefix = published[2]
    cut, agenda, view = _dispatch(case, prefix)
    _check_filing(case, view, agenda, arm)
    items, bays = sorted(view["order"]["items"]), sorted(view["bays"])
    require(len(items) == 2 and len(bays) == len(set(bays)) == 8, "menu is not 8x8")
    packet = encode_request(render_turn(instruction, view, prefix[0]))
    if published is None:
        scores: dict[str, int] = {}
        for left, right in product(range(8), repeat=2):
            routes = dict(zip(items, (bays[left], bays[right]), strict=True))
            graded = native._commit(case, prefix, {"op": "commit", "routes": routes})
            scores[f"{left}:{right}"] = _expired_grade(graded)["reward"]
        world = World(name, 1, packet, scores)
    else:
        world, count, _ = published
        require(
            world.observation == packet and count["chains_supported"] == SUPPORT[arm],
            "dispatch view differs from the published native control",
        )
    require(
        len(world.scores) == 64 and sum(world.scores.values()) == 1,
        "native dispatch does not have exactly one winner",
    )
    followed = policies.follow(view)
    require((followed is None) is (arm != "current"), "chain following differs")
    require(
        followed is None or world.scores[_label(followed, items, bays)] == 1,
        "current chain following misses the native winner",
    )
    return {
        "world": world,
        "prefix": prefix,
        "agenda": agenda,
        "view": view,
        "menu": (items, bays),
        "labels": _labels(cut, arm)[1],
    }


def _stale_reference(selected: dict[str, Json], rows: list[Json]) -> Json:
    actions = [stale_commit(row["view"]) for row in rows]
    require(actions[0] == actions[1], "stale reference differs across maps")
    rewards: dict[str, int] = {}
    matched: dict[str, int] = {}
    labels: list[Json] = []
    for name, row, action in zip(MAPS, rows, actions, strict=True):
        cut = native._commit(selected[name], row["prefix"], action)
        result = _expired_grade(cut)
        require(result["reward"] == result["correct_items"] == 0, "stale route scored")
        cell = _label(action, *row["menu"])
        require(row["world"].scores[cell] == 0, "stale reference disagrees with grid")
        items, compact = _labels(cut, "expired")
        flags = [item["choice_matches_expired_depot_chain"] is True for item in items]
        require(len(flags) == 2 and all(flags), "stale route misses an expired chain")
        rewards[name], matched[name] = result["reward"], sum(flags)
        labels.append(compact)
    require(labels[0] == labels[1], "stale reference labels differ across maps")
    return {
        "native_reward": rewards,
        "correct_items": 0,
        "registrar_supported": 2,
        "depot_supported": 0,
        "expired_matching_items_by_map": matched,
        "version_labels": labels[0],
    }


def _treatment(selected: dict[str, Json], arm: str, instruction: str) -> Json:
    rows = [_row(selected[name], arm, name, instruction) for name in MAPS]
    first, second = rows
    for key in ("prefix", "agenda", "menu", "labels"):
        require(first[key] == second[key], f"{key} discloses the hidden map")
    require(first["view"]["order"] == second["view"]["order"], "order differs by map")
    worlds = [row["world"] for row in rows]
    original, swapped = (world.scores for world in worlds)
    require(
        not any(original[key] and swapped[key] for key in original),
        "physical winner does not differ by map",
    )
    shared = worlds[0].observation == worlds[1].observation
    require(shared is (arm != "current"), "final packet map dependence differs")
    analysis = analyze(worlds, 1)
    design = (analysis["observations"], analysis["observed"])
    require(design == (CLASSES[arm], OPTIMUM[arm]), "paired optimum differs by arm")
    stamps = {world.name: request_stamp(world.observation) for world in worlds}
    summary: Json = {
        "analysis": analysis,
        "maps_share_final_packet": shared,
        "request_stamps": stamps,
        "registrar_supported": 2,
        "depot_supported": SUPPORT[arm],
        "version_labels": first["labels"],
    }
    if arm == "expired":
        summary["stale_reference"] = _stale_reference(selected, rows)
    return {"worlds": worlds, "summary": summary, "first": first}


def _check_across(arms: dict[str, Json]) -> int:
    current = arms["current"]
    for arm in arms.values():
        first, reference = arm["first"], current["first"]
        require(first["prefix"][:2] == reference["prefix"][:2], "upstream differs")
        require(first["view"]["order"] == reference["view"]["order"], "order differs")
        for world, control in zip(arm["worlds"], current["worlds"], strict=True):
            require(world.scores == control.scores, "payoffs depend on the filing")
    sizes = {len(world.observation) for arm in arms.values() for world in arm["worlds"]}
    require(len(sizes) == 1, "final loader request lengths differ")
    return sizes.pop()


def _comparisons(matrices: dict[str, list[World]]) -> Json:
    comparisons: Json = {}
    for (before, after), expected in COMPARISONS.items():
        comparison = compare(matrices[before], 1, matrices[after], 1)
        observed = (comparison["relation"], comparison["gain"])
        require(observed == expected, f"{before} to {after} partition differs")
        comparisons[f"{before}_to_{after}"] = comparison
    return comparisons


def certify_freshness(start: int) -> Json:
    seed, checked, base, rejections = _search(start)
    instruction = native._INSTRUCTION.read_text(encoding="utf-8")
    selected = _maps(base, instruction)
    arms = {arm: _treatment(selected, arm, instruction) for arm in TREATMENTS}
    request_bytes = _check_across(arms)
    matrices = {name: arm["worlds"] for name, arm in arms.items()}
    native_commits = sum(len(row.scores) for rows in matrices.values() for row in rows)
    require(native_commits == 384, "incomplete dispatch enumeration")
    focal = min(base["briefs"], key=lambda brief: brief["id"])
    total = sum(brief["weight"] for brief in base["briefs"])
    return {
        "schema": SCHEMA,
        "objective": "both_native_dispatch_destinations_correct",
        "model_calls": 0,
        "harbor_trials": 0,
        "development_seed": seed,
        "search_limit": SEARCH_LIMIT,
        "candidates_checked": checked,
        "rejections": rejections,
        "case_commitments": {
            name: native.case_commitment(case) for name, case in selected.items()
        },
        "focal_probability": str(Fraction(focal["weight"], total)),
        "initial_loader_inputs_identical": True,
        "final_owner_request_bytes": request_bytes,
        "cross_policy_packet_equality": "not_asserted",
        "native_commits": native_commits,
        "treatments": {name: arm["summary"] for name, arm in arms.items()},
        "comparisons": _comparisons(matrices),
        "scope": SCOPE,
    }
