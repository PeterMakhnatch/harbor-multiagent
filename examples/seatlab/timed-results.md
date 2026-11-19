# Hourly native handoff control

On November 19, 2026, 8 independent Harbor trials completed in their original hourly slots, from 4:15 AM ET through 11:15 AM ET. We registered a 15-minute window per slot. The last world finished 22.7 seconds or less after its scheduled start. We did not replace or backfill any slot. The 8 trial UUIDs and native result hashes are distinct.

The registered scripted control used seed `1009`, condition `reordered`, and 4 shipment episodes in each trial. The 7 measured projections agreed across all 8 trials: latent shipments, both world-derived role views, accepted inventory reports, dispatch decisions, scores and rewards. The verifier found 0 receipt integrity errors. [Machine-readable evidence for every slot](results/timed-control.json) · [offline reducer](timed_evidence.py).

This checks one machine, one task, one seed and a deterministic policy over 7 hours. It does not establish cross-host or model reproducibility. The views come from the world, not an acknowledgement by the oracle. The plan contained no hosted model arm. The 32 shipment episodes are repeated measurements, not 32 independent task seeds.

Recompute from an original retained schedule directory:

```sh
python examples/seatlab/timed_evidence.py /path/to/hourly-stability
```

The reducer checks each receipt against the predeclared window. An absent or expired slot is not silently filled. The exact original plan digest is `6cbf0c01fefee9d5bc22c11fcfd828069a01fc52b0fd12d5b087278a7df03eb0`.
