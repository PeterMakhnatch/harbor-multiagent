# Can an unchanged actor see a different world?

This is a SeatLab integration for [the independent two-chef Harbor example](https://github.com/PeterMakhnatch/harbor-multiagent/tree/fa357189438d0d8c7d2ff4f97b00afb83d75dfa5/examples/tasks/cramped-room-parity). It is not a replacement kitchen, new benchmark, or change to that branch. Its task owner already reports successful native tape and wait controls. We use those known controls to test a different claim: what offline policy replay cannot establish about an unchanged partner.

## Register before launch

Run 2 independent blocks. Each block contains 3 fresh native trials: both actors use the scripted tape (`TT`); only chef0 waits (`WT`); only chef1 waits (`TW`). Order the first block TT, WT, TW. Reverse that order in the second block. This is **one** fixed kitchen and deterministic pair of actors, not 6 independent tasks. Use 38 simultaneous turns and the original game, native action sequence, layout, joint-transition log and verifier. No models, remote containers, extra trials to replace missing data, or paid services. Do not use another worker's private artifacts.

For each accepted action, bind the actor's digest of its actual `/state` response to the world's state at that tick. Save the acknowledgments separately. Do not change the original joint transition or score to emit an observation. The action policy still consults only the tick. Recording the full response does not show that the actor used every field. Keep old trials without these receipts unbound.

Align `TT` against each intervention **within the same block**, by role and case hash. Also align `TT` between the two blocks as an independent unchanged control. Compare canonical input before action. The changed chef should differ on the first turn at equal input. The unchanged chef can keep the same action sequence even after its partner changes the world. Test for its **first changed input**, not a fictitious decision error. Record the first observed boundary, equal-prefix length, full native reward and actual deliveries. If the inputs remain equal, report that instead. Missing evidence stays missing.

Separately, run a source-only `wait` policy on each `TT` chef. It cannot see any target observation or score. Its first difference forecasts only the changed chef's next action on the common history. It does not forecast the partner's later inputs, soup deliveries, or reward. The independent native grade establishes those results. Report both UUIDs for every comparison; never count the same two-chef trial twice as two team outcomes.
