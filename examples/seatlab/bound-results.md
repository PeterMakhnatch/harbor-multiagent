# What the native observation receipts establish

We ran the [registered test](bound-protocol.md) on implementation `cae33f4c06a1faa43e770ffe33e4107e3859b49f`, using the real Olam `harbor trial start` command, Docker Compose and the two separate role clients. This is a **transport and retention test**, not a comparison of language models. It uses 9 new native trials. Each trial has 12 shipment decisions.

| Comparison | Native cells per side | Compared active role views | Canonical input | Accepted client receipt |
| --- | ---: | ---: | --- | --- |
| Fresh native panel A vs fresh native panel B | 4 | 8 | All match | All 8 comparisons match on both sides |
| Retained historical panel A vs fresh native panel A | 4 | 8 | All match | Historical side has no receipt; new side has 8 |
| Pooled control | 1 | 1 | Dispatch sees its pooled input | Only dispatch acknowledges; no inventory action is invented |

The two registered seeds are 1001 and 1003. Both panels use the same two conditions, `fresh` and `reordered`, and the same arrival-order control. Both comparisons agree on all downstream stages: reports, choices and native reward. Four old and four new trials do **not** make eight independent seeds. The pooled trial has UUID `93a52965-efde-434f-ab7d-512c844703d3`.

The server checks the client's view digest against the current role view before committing its action. An accepted receipt supports an exact canonical role-view statement. It does not prove the client read the input, chose rationally, received the verifier's world, or communicated with another seat. Missing historical receipts remain missing even when replay reconstructs an identical input.

Machine reports: [new vs new](results/bound-v-bound.json) · [historical vs new](results/historical-v-bound.json). These reports contain no raw host paths or model credentials.