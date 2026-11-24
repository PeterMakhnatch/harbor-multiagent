from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Kind = Literal[
    "native-model-team",
    "native-hybrid-team",
    "partial-native-operator-cohort",
    "stock-business-counterfactual",
    "native-replay-team",
    "secondary-native-receipt-analysis",
]

_RESULTS = "examples/seatlab/results"
_WAIT = f"{_RESULTS}/wait-for-peer"
_OPERATOR = f"{_WAIT}/operator-example"
_HANDOFF = f"{_RESULTS}/market-handoff"


@dataclass(frozen=True, slots=True)
class Study:
    id: str
    question: str
    kind: Kind
    guide: str
    module: str
    argv: tuple[str, ...]
    report: str
    exit_code: int
    sha256: str


STUDIES: tuple[Study, ...] = (
    Study(
        id="certified-reporters",
        question="Can a local booker skip real reporting sessions on new stock calendars?",
        kind="native-hybrid-team",
        guide="examples/seatlab/certified-reporters.md",
        module="examples.seatlab.fanin.certified_panel",
        argv=(f"{_RESULTS}/certified-reporters/rows.json",),
        report=f"{_RESULTS}/certified-reporters/panel.json",
        exit_code=0,
        sha256="93488d7c23bc53b66a8242f1873b0be363a96e1ac9ea540d98b5394bc22de770",
    ),
    Study(
        id="reporter-tokens",
        question="Which matched reporting sessions account for the difference in client token totals?",
        kind="secondary-native-receipt-analysis",
        guide="examples/seatlab/certified-reporters.md",
        module="examples.seatlab.fanin.reporter_tokens",
        argv=(
            f"{_RESULTS}/certified-reporters/rows.json",
            "--owner-counters",
            f"{_RESULTS}/certified-reporters/owner-counters.json",
        ),
        report=f"{_RESULTS}/certified-reporters/token-partition.json",
        exit_code=0,
        sha256="ed6a6e4a9221cc8e788baae5dbc45c7c36fcdff41c7bedafc417a5c208d452aa",
    ),
    Study(
        id="peer-fanin",
        question="Can a four-seat Harbor booker leave an unneeded reporter idle?",
        kind="native-model-team",
        guide="examples/seatlab/peer-fanin.md",
        module="examples.seatlab.fanin.panel",
        argv=(f"{_RESULTS}/peer-fanin/rows.json",),
        report=f"{_RESULTS}/peer-fanin/panel.json",
        exit_code=0,
        sha256="ff13d085734c2c9f056ebc6bf92be3b311c75a3db975f0ca47d5fa4f861e9b20",
    ),
    Study(
        id="gather-time",
        question="How much native waiting can three independent reporters overlap?",
        kind="native-model-team",
        guide="examples/seatlab/gather-time.md",
        module="examples.seatlab.fanin.fanout_panel",
        argv=(f"{_RESULTS}/gather-time/rows.json",),
        report=f"{_RESULTS}/gather-time/panel.json",
        exit_code=0,
        sha256="1b6fddeb382fef63e7c3db49dc65ca2ba10604a8be42b55784eb0fd1eef7611d",
    ),
    Study(
        id="join-recovery",
        question="Can a team finish from verified reports when its booker model is withheld?",
        kind="native-hybrid-team",
        guide="examples/seatlab/join-recovery.md",
        module="examples.seatlab.fanin.join_panel",
        argv=(f"{_RESULTS}/join-recovery/rows.json",),
        report=f"{_RESULTS}/join-recovery/panel.json",
        exit_code=0,
        sha256="8c988df0ad8f5c25633225e65e1d6ef33042cc76e027369fa612adbcfe26d10c",
    ),
    Study(
        id="calendar-recheck",
        question="Does a reminder or automatic delivery improve on an optional peer read?",
        kind="native-model-team",
        guide="examples/seatlab/shared-read.md",
        module="examples.seatlab.wait_peer.panel",
        argv=(f"{_WAIT}/calendar-subscription-rows.json",),
        report=f"{_WAIT}/calendar-panel.json",
        exit_code=0,
        sha256="2fe16f757a5a5bff978cbb0c8fb68b399d762a2f5e1e62a8eb814f4ae29f11fd",
    ),
    Study(
        id="operator-cohort",
        question="Can an independent operator run the peer-read comparison on new worlds?",
        kind="partial-native-operator-cohort",
        guide="examples/seatlab/shared-read.md",
        module="examples.seatlab.wait_peer.panel",
        argv=(
            f"{_OPERATOR}/calendar-rows.json",
            "--manifest",
            f"{_OPERATOR}/calendar-registration.json",
            "--sha256",
            "928d2a53d733ee01e8a200508db1fa6ef164a933aadb81672bd9d65154138f12",
        ),
        report=f"{_OPERATOR}/calendar-panel.json",
        exit_code=0,
        sha256="6123daddd04a184a5b8166c1a41795261997202d7b2320c2c1ee5bcbc8ff1c35",
    ),
    Study(
        id="private-pricing",
        question="Does a private demand survey justify a pricing handoff?",
        kind="stock-business-counterfactual",
        guide="examples/seatlab/private-price-survey.md",
        module="examples.seatlab.market_handoff.private_price_panel",
        argv=(f"{_RESULTS}/market-handoff", f"{_RESULTS}/private-price-survey/rows"),
        report=f"{_RESULTS}/private-price-survey/panel.json",
        exit_code=0,
        sha256="90bb5dc93ad6074a91f6fb55fda531579070f89b22d2642dd3d9fa70bd1f2560",
    ),
    Study(
        id="deployment-queue",
        question="Should two operators use a teammate's evidence to assign one early deployment?",
        kind="stock-business-counterfactual",
        guide="examples/seatlab/continuation-study.md",
        module="examples.seatlab.continuation.deploy_queue",
        argv=("panel", f"{_RESULTS}/deploy-queue-cells"),
        report=f"{_RESULTS}/deploy-queue-panel.json",
        exit_code=1,
        sha256="bdb23fae12e691cbd2a7a32001cbadc168d5d23ef427d020163d39c2333ced1d",
    ),
    Study(
        id="native-business-replay",
        question="Do 24 recorded sales and finance teams keep their grades in native Harbor?",
        kind="native-replay-team",
        guide="examples/seatlab/market-handoff.md",
        module="examples.seatlab.market_handoff.native_panel",
        argv=("score", _HANDOFF, f"{_HANDOFF}/native-panel.json"),
        report=f"{_HANDOFF}/native-panel-score.json",
        exit_code=0,
        sha256="6f88f500316f8f8439e304d25e6efb131034d73d1b94ac2d9195ae9f1253a08d",
    ),
    Study(
        id="native-business-repeat",
        question="Can another operator reproduce all recorded choices and stock grades in new native trials?",
        kind="native-replay-team",
        guide="examples/seatlab/market-handoff.md",
        module="examples.seatlab.market_handoff.native_repeat_panel",
        argv=(
            "score",
            _HANDOFF,
            f"{_HANDOFF}/native-panel.json",
            f"{_HANDOFF}/native-repeat-receipts.json",
        ),
        report=f"{_HANDOFF}/native-repeat-score.json",
        exit_code=0,
        sha256="7bc0f088d57527694cca3b14bb65ca609da28eca177d4c5c25f6fd52b4455e32",
    ),
    Study(
        id="native-report-budget",
        question="Does asking sales for a shorter report help it reach finance without costing profit?",
        kind="native-model-team",
        guide="examples/seatlab/report-budget.md",
        module="examples.seatlab.report_budget.panel",
        argv=(f"{_RESULTS}/report-budget/rows.json",),
        report=f"{_RESULTS}/report-budget/result.json",
        exit_code=0,
        sha256="2f5f10f542a5fc20273c486c922eb38f71f9db7d3fd1fdcabcf48d052cc61bc3",
    ),
)

assert len({study.id for study in STUDIES}) == len(STUDIES)
