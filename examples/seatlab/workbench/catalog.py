from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Kind = Literal[
    "native-model-team",
    "native-hybrid-team",
    "partial-native-operator-cohort",
    "stock-business-counterfactual",
]

_RESULTS = "examples/seatlab/results"
_WAIT = f"{_RESULTS}/wait-for-peer"
_OPERATOR = f"{_WAIT}/operator-example"


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
)

assert len({study.id for study in STUDIES}) == len(STUDIES)
