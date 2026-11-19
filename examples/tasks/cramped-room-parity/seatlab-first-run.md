# Native kitchen study: the first runtime did not start either chef

The [registered 6-trial study](seatlab-protocol.md) produced **0 measured team outcomes out of 6 native trials**. This is not a zero reward. None of the 12 chef processes started the policy. Each returned exit code `126` with `/solution/solve.sh: Permission denied`. No trial produced the joint transition log, the actor acknowledgments, or a native reward. All 6 report `RewardFileNotFoundError`. The exporter correctly declined to reconstruct missing actor views from the game rules.

| Cell | Retained native trial UUID | Native grade |
| --- | --- | --- |
| A-TT | `effe422e-9fc9-4daf-9643-d7e054d473cb` | Unknown |
| A-WT | `f017c16b-5434-4e4a-b342-7f77b16d8ad6` | Unknown |
| A-TW | `4e3920fc-b78b-4ad1-90ee-b99af2dd4476` | Unknown |
| B-TW | `16b8195c-8fb9-408e-9c98-ae579704da69` | Unknown |
| B-WT | `17c679f5-6a40-4572-8d34-7d98f0d5eb08` | Unknown |
| B-TT | `3f731853-9a19-4efc-af40-49b5ca6f0c21` | Unknown |

The study ID is `1c89ff26-d076-40ca-969b-ee278d47f40c`. The registered task capsule digest is `2b7961c3e2d7cb17d3af106b54b2e31e68d038aecae015d463725ef37c15aa45`. The path-free collector output has SHA-256 `fce27a91bafcbe73c5a4f95c439446db12af2a4ea49f30b6d2c9bf364b5e073c`.

The experiment establishes a runtime prerequisite, not a result about team coordination. Native process creation, actor execution and team grading are three different events. The first did not establish the other two. A separate runtime control must keep these 6 trials in this denominator and use new registrations. See the [personal-fork runtime fix](https://github.com/PeterMakhnatch/harbor-multiagent/pull/7). No upstream change is required to inspect the failure.
