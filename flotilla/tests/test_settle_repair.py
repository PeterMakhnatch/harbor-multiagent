"""Regression cases from the first live settle playtest and deterministic desk state transitions."""

from types import SimpleNamespace

from conftest import FLOTILLA, load

FAMILY = load(FLOTILLA / "seams" / "settle.py")
POLICIES = load(FLOTILLA / "policies" / "settle.py")


def test_live_list_reports_do_not_include_rule_or_order():
    cases = [
        ("p2 blocked W-TG W-ZN. Please share blocked lists. Suggest p1 announce common feasible window in round 2 after receiving lists; all follow it in round 3.", "TG ZN"),
        ("p1 blocked W-KL W-ZN; available W-BH W-MF W-TG W-VH. Please share blocked lists. Suggest all choose alphabetically first common available window, and confirm round 2 before filing.", "KL ZN"),
        ("p3 blocked: W-BH only. Please share full blocked lists. Suggest all file lexicographically first window not blocked by any desk, after all lists received.", "BH"),
        ("p2 blocked: W-MT only. Rule proposal: everyone post blocked list in R1; in R3 file the alphabetically first window not blocked by any desk. p2 last used W-ZN.", "MT"),
        ("p1 blocked: W-BZ W-DN W-KN. Proposal: all post blocked lists in R1; then everyone files the FIRST window in list order (BZ,DN,HC,HK,KN,MT,VH,ZN) blocked by nobody. File in R3.", "BZ DN KN"),
        ("p5 blocked: W-DN W-HK W-MT. OK: W-BZ W-HC W-KN W-VH W-ZN. Rule: everyone post blocked list in R1; in R2 file the alphabetically first window nobody blocked.", "DN HK MT"),
        ("p4 blocked: W-HK only. Proposal: everyone posts blocked list; we all file the alphabetically first window blocked by nobody. p4 OK with W-VH (last used) if free for all.", "HK"),
        ("p3 blocked: W-DN only. Proposal: everyone post blocked list in R1; in R2 file the first window alphabetically that no desk reported blocked. p3 last used W-HK, fine with it too.", "DN"),
        ("p2 blocked: W-RC. Rule: all desks file the window that comes first in alphabetical order among the windows that no desk has blocked. We file in round 4.", "RC"),
        ("p3 blocked: W-BR, W-XV. Rule: all desks file the window that comes first in alphabetical order among the windows that no desk has blocked. We file in round 4.", "BR XV"),
        ("p1: blocked W-QV only. Propose W-XN. Please each send your full blocked list. Rule: file the first window in order W-XN,W-BR,W-GD,W-RC,W-XV that no desk has blocked.", "QV"),
    ]
    for text, codes in cases:
        windows = sorted(set(FAMILY.mentions(text, [f"W-{a}{b}" for a in FAMILY.LETTERS for b in FAMILY.LETTERS])))
        expected = {f"W-{code}" for code in codes.split()}
        assert FAMILY.parse_blocked(text, windows) == expected, text
        assert FAMILY.tells(text, windows, expected)
    nonreports = [
        "pick the first window in order W-JF,W-KB,W-LK,W-RK that no desk blocks",
        "pick = first window in order W-KL,W-BH,W-MF,W-VH not blocked by anyone",
        "prefer W-CR", "Default pick W-TG if it works for all", "prefer W-SQ if free",
        "Last used W-GH but no preference over consensus",
        "All blocked lists received. Common available windows W-MF and W-VH; lexicographically first is W-MF. All desks please file W-MF in round 3. I will file W-MF in round 3.",
        "All lists received: common feasible W-MF and W-VH. Agree alphabetically first W-MF. I will file W-MF in round 3 after confirming p1/p2 agree.",
        "Confirmed full lists p1 W-KL W-ZN, p2 W-TG W-ZN, p3 W-BH. Common W-MF W-VH. Agree with both desks: file W-MF (alphabetically first common available window).",
        "p1: union blocked = BZ DN HK KN MT. First free for all = W-HC. p1 files W-HC now.",
        "p1: I drop my rule and agree with yours (alphabetical). Blocked overall: W-QV,W-RC,W-BR,W-XV. Free: W-GD,W-XN. All file W-GD in round 4.",
        "Confirm: p3 files W-GD in round 4.", "Confirm: p2 files W-BR in round 4.",
        "p1: please file W-JB, not W-SQ", "p1 blocked W-JF?",
    ]
    windows = [f"W-{a}{b}" for a in FAMILY.LETTERS for b in FAMILY.LETTERS]
    for text in nonreports:
        assert FAMILY.parse_blocked(text, windows) is None, text


class Own(dict):
    lapse_percent = None


def test_scripted_desk_holds_and_replaces_reports():
    windows = ["W-BR", "W-GD", "W-QV", "W-RC", "W-XN", "W-XV"]
    own = Own(desks=["p1", "p2", "p3"], windows=windows, blocked=["W-RC"], rounds=4)
    desk = POLICIES.Desk("careful", SimpleNamespace(seat="p2"), own, FAMILY)
    desk.decide(1)

    def hear(sender, text, tick=0):
        desk.absorb({"new_messages": [{"from": sender, "text": text, "t": tick}]})

    hear("p1", "Rule: the first window in order W-XN,W-BR,W-GD,W-RC,W-XV that nobody has blocked.")
    hear("p3", "p3 blocked: W-BR, W-XV.")
    question, filing = desk.decide(2)
    assert "p1" in question and "?" in question and filing is None
    hear("p1", "p1 blocked: W-QV, W-GD.", 1)
    assert desk.target() == "W-XN"
    hear("p1", "Correction: p1 blocked W-QV only. Rule: use the first window in order W-XN,W-BR,W-GD,W-RC,W-XV that no desk has blocked.", 2)
    assert desk.lists["p1"] == {"W-QV"}
    assert desk.decide(4) == (None, "W-GD")
    hear("p1", "OK W-GD.", 2)
    assert desk.lists["p1"] == {"W-QV"}
    assert not desk.allowed("W-BR")
    hear("p1", "p1 blocked: W-GD,W-QV,W-XN.", 3)
    assert desk.decide(4)[1] is None


def test_receipts_distinguish_rule_filing_and_departure_from_rule(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "family", FAMILY)
    extension = load(FLOTILLA / "world" / "ext" / "settle.py")
    desks = ["p1", "p2", "p3"]
    windows = ["W-BR", "W-GD", "W-QV", "W-RC", "W-XN", "W-XV"]
    blocked = {"p1": ["W-QV"], "p2": ["W-RC"], "p3": ["W-BR", "W-XV"]}
    params = {"desks": desks, "windows": windows, "blocked": blocked, "rounds": 4, "delay": 1, "subject": "p1",
              "last_time": dict.fromkeys(desks, "W-XN")}
    task = {"seats": desks, "params": params}

    def play(filings, *, correction=False, miss=False, named=False):
        events = []

        def event(kind, seat, round_, data):
            events.append({"seq": len(events) + 1, "kind": kind, "seat": seat, "t": round_ - 1, "data": data})

        for desk in desks:
            rule = POLICIES.ALPHA_RULE
            if correction and desk == "p1":
                rule = "Rule: file the first window in order W-XN,W-BR,W-GD,W-RC,W-XV that no desk has blocked."
            event("send", desk, 1, {"id": desk, "text": f"{desk} blocked: {', '.join(blocked[desk])}. {rule}"})
        event("settle_round", None, 1, {"round": 1})
        for desk in desks:
            ids = [other for other in desks if other != desk and not (miss and desk == "p2" and other == "p3")]
            event("deliver", desk, 2, {"ids": ids})
        if correction or named:
            event("send", "p1", 2, {"id": "correction", "text": "I drop my rule and agree with yours (alphabetical). "
                  "Blocked overall: W-QV,W-RC,W-BR,W-XV. Free: W-GD,W-XN. All file W-GD in round 4."})
            event("settle_round", None, 2, {"round": 2})
            for desk in desks[1:]:
                event("deliver", desk, 3, {"ids": ["correction"]})
            event("send", "p3", 3, {"id": "confirm3", "text": "Confirm: p3 files W-GD in round 4."})
            event("send", "p2", 3, {"id": "confirm2", "text": f"Confirm: p2 files {filings[1]} in round 4."})
            event("settle_round", None, 3, {"round": 3})
            for desk in desks:
                event("deliver", desk, 4, {"ids": [item for sender, item in (("p2", "confirm2"), ("p3", "confirm3"))
                                                    if desk != sender]})
        for desk, window in zip(desks, filings, strict=True):
            event("settle_file", desk, 4 if correction or named else 2,
                  {"desk": desk, "window": window, "round": 4 if correction or named else 2})
        return extension.score(task, events)

    score = play(["W-GD"] * 3)
    assert score["reward"] == 1 and score["premature_filing"] == [] and score["filed_on_rule"] == desks
    assert score["first_divergent"] is None
    score = play(["W-GD"] * 3, miss=True)
    assert score["premature_filing"] == ["p2"] and score["filed_on_rule"] == ["p1", "p3"]
    score = play(["W-GD"] * 3, named=True)
    assert score["filed_on_rule"] == []
    score = play(["W-GD", "W-BR", "W-GD"], correction=True)
    assert score["reward"] == 0 and score["first_divergent"] == "p2" and score["premature_filing"] == []
    assert score["unparsed"] == []
    score = play(["W-GD", "W-BR", "W-GD"], correction=True, miss=True)
    assert score["shape"] == "unpooled" and score["premature_filing"] == ["p2"]
    assert score["first_divergent"] is None


def test_private_order_pair_has_distinct_firsts_and_only_one_visible_clause(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "family", FAMILY)
    extension = load(FLOTILLA / "world" / "ext" / "settle.py")
    for level in FAMILY.LEVELS:
        for i in range(100):
            inst = FAMILY.generate(421126, i, None, level)
            explicit, implicit = [FAMILY.extension_arm(inst, arm, 240, 4) for arm in
                                  ("team-ownorder", "team-implicit")]
            params = explicit["params"]
            assert params == implicit["params"]
            assert explicit["briefings"] == implicit["briefings"]
            common = set(params["free_for_all"])
            assert len(common) >= 2
            assert len({next(code for code in order if code in common) for order in params["orders"].values()}) > 1
            assert len({tuple(order) for order in params["orders"].values()}) == len(params["desks"])
            world = extension.create({"params": params, "seats": params["desks"]},
                                     SimpleNamespace(tick=0, record=lambda *_: None))
            for desk in params["desks"]:
                a, b = explicit["instructions"][desk], implicit["instructions"][desk]
                assert a.replace(FAMILY.ORDER_CLAUSE, "", 1) == b
                assert "Every desk has this same list" not in a + b
                assert FAMILY.ORDER_FACT + FAMILY.ORDER_CLAUSE + "." in a and FAMILY.ORDER_FACT + "." in b
                assert world.desk(desk)[1]["windows"] == explicit["briefings"][desk]["windows"] == params["orders"][desk]
