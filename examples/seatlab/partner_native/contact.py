from typing import Any

from ...joint_reopen.observation import OBSERVATION_KEYS, ROLE_KEYS, ROLES

REGISTERED = 6
OUTCOMES = {
    (None, None, None, 0): "no_vote_pair",
    (False, None, None, 0): "split_vote",
    (True, False, False, 0): "same_unavailable",
    (True, True, True, 1): "repaired",
}
RECEIPTS = {
    (True, True): "both",
    (True, False): "east_only",
    (False, True): "west_only",
    (False, False): "neither",
}


def _outcome(observation: dict[str, Any]) -> str:
    joint = observation["final_joint"]
    feasible = observation["final_feasible"]
    repaired = observation["repaired_invalid_incumbent"]
    reward = observation["reward"]
    if (
        any(
            value is not None and type(value) is not bool
            for value in (joint, feasible, repaired)
        )
        or observation["kept_preferred_incumbent"] is not None
        or observation["incumbent_feasible"] is not False
        or type(reward) is not int
    ):
        raise ValueError("invalid final observation fields")
    outcome = OUTCOMES.get((joint, feasible, repaired, reward))
    if outcome is None:
        raise ValueError("inconsistent final observation fields")
    return outcome


def _receipt(observation: dict[str, Any]) -> str:
    roles = observation["roles"]
    if type(roles) is not dict or roles.keys() != set(ROLES):
        raise ValueError("observation must name both roles")
    delivered: dict[str, bool] = {}
    for role in ROLES:
        report = roles[role]
        if type(report) is not dict or report.keys() != ROLE_KEYS:
            raise ValueError("missing or invalid role fields")
        before = report["peer_report_before_vote"]
        if before is not None and type(before) is not bool:
            raise ValueError("invalid peer_report_before_vote")
        delivered[role] = before is True
    return RECEIPTS[delivered["east"], delivered["west"]]


def summarize(observations: list[dict[str, Any] | None]) -> dict[str, Any]:
    if type(observations) is not list or len(observations) != REGISTERED:
        raise ValueError("a pair requires six registered observation slots")
    outcomes = dict.fromkeys(OUTCOMES.values(), 0)
    receipts = dict.fromkeys(RECEIPTS.values(), 0)
    matrix = {outcome: dict.fromkeys(receipts, 0) for outcome in outcomes}
    observed = 0
    for observation in observations:
        if observation is None:
            continue
        if type(observation) is not dict or observation.keys() != OBSERVATION_KEYS:
            raise ValueError("missing or invalid observation fields")
        if observation["fixture"] != "both" or observation["channel"] != "deliver":
            raise ValueError("unsupported observation condition")
        outcome = _outcome(observation)
        receipt = _receipt(observation)
        outcomes[outcome] += 1
        receipts[receipt] += 1
        matrix[outcome][receipt] += 1
        observed += 1
    return {
        "registered": REGISTERED,
        "observed": observed,
        "unknown": REGISTERED - observed,
        "outcomes": outcomes,
        "receipt_before_vote": receipts,
        "outcome_by_receipt": matrix,
    }
