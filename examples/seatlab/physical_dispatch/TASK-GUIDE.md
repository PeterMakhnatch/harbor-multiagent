# Exhibit Relay: physical dispatch control

This is `harbor/exhibit-relay-physical`, a distinct, opt-in native task derived from `harbor/exhibit-relay`. It uses 3 isolated roles, the original weighted lottery, the original role instructions and the original physical verifier. No model result accompanies it.

The loader nominates forecast briefs. The registrar can file exhibit-to-crate records. The depot can file crate-to-bay records. After both filings, the loader receives the selected order and commits 2 physical routes. Only 2 correct routes earn 1. A correct guess counts. A complete file with a wrong dispatch does not count.

Only the main world receives `EXHIBIT_BAY_MAP`: `original` or `swapped`. Both settings use the same seed, `fork` profile, `draw` layout and `linked` mode. The swap exchanges the destinations of the two crates for the first public brief, ordered by its ID. It changes depot history values consistently across epochs. It does not change labels, weights, the lottery result, source card IDs or the physical grading rule. The first public brief does not necessarily win the lottery.

In the offline development control, the loader nominates this public brief and the registrar files its 2 current records. The depot files 2 relevant records or 2 other current records. The optional controlled-receiver roster also uses these policies for the first 3 actions. An unrestricted roster retains every original legal move.

Use the `examples/seatlab/physical_dispatch` author tools in the fork checkout for qualification and native input preparation. The retained stock protocol files describe the source task. Its prior seed-only enrollments and archive receipts do not certify this variant. Keep the map setting, case, output locks and frozen control cut outside the participants’ contexts.
