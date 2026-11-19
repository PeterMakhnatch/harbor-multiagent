# Two upstream candidates, both reproduced in Docker

No pull request has been sent to Olam Labs. These branches are on a personal fork. They do not depend on the study tasks or the offline workbench.

| Candidate | Original behavior | Observed after the change |
| --- | --- | --- |
| [Honor a reserved multi-seat trial UUID](https://github.com/PeterMakhnatch/harbor-multiagent/compare/6aeb609e31fd77e5f89ce3bcb018db8045520fb7...0e2b6a1289681996a691bbecec4448eaada083e8) | `Trial.create(..., trial_id=ID)` discards `ID` for a multi-seat trial. A correct score can no longer join to its reserved ID. | Real two-seat trial requested, returned and retained `e71aee8b-5055-5c70-bfc2-033e0133ad48`. The unmodified task scored 1/1. All 25 tests in the seat lifecycle module pass on Python 3.13. |
| [Stage private oracle inputs as the seat user](https://github.com/PeterMakhnatch/harbor-multiagent/compare/6aeb609e31fd77e5f89ce3bcb018db8045520fb7...61023ae7389909486ea4cfd67764db74d43287a1) | Both nonroot `hello-seats` oracles exit 126 when the host solution directory is mode `0700`. Harbor records a shared 0. Copying the same bytes with ordinary Git archive modes gives 1. | Native trial `b88ef8c9-28e8-41e9-a76e-649f25ef6adc` scores 1/1 with both nonroot oracles and the original private checkout. The host permissions remain unchanged. The 15 focused tests pass. |

The second candidate prepares a clean destination and transfers only the solution files. It does not run the task's solution as root or widen the host checkout. The native proof covers Docker on this machine, not every custom backend. See [the original controls](baselines.md). Neither change proves reproducibility for arbitrary agents or environments.