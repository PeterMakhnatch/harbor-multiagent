# Exhibit Relay: joint handoff control

This is `harbor/exhibit-relay-joint`, a distinct, opt-in native task derived from `harbor/exhibit-relay`. It uses 3 isolated roles, the original weighted lottery, the original role instructions with one added loader sentence, and the original physical verifier. No model result accompanies it.

The loader nominates forecast briefs. The registrar can file exhibit-to-crate records. The depot can file crate-to-bay records. After both filings, the loader receives the selected order and commits 2 physical routes. Only 2 correct routes earn 1. A correct guess counts. A complete file with a wrong dispatch does not count.

Only the main world service receives 3 settings. All settings use the same seed, `fork` profile, `draw` layout and `linked` mode.

- `EXHIBIT_REGISTRY_MAP`: `original` or `swapped`. The swap exchanges the 2 crates that hold the exhibits of the first public brief, ordered by its ID, at that brief's epoch. It changes registrar values consistently across epochs.
- `EXHIBIT_BAY_MAP`: `original` or `swapped`. The swap exchanges the 2 bays that hold those 2 crates in the original case, at the same epoch. It changes depot values consistently across epochs.
- `EXHIBIT_CHANNELS`: `neither`, `registrar`, `depot` or `both`. It names the filings whose record values reach the loader. The relay replaces each other filed record value with `?` characters of the same UTF-8 length. Record IDs, keys, intervals, order and all other view fields stay the same.

The 2 maps vary independently. Neither map changes labels, weights, briefs, the lottery result, record IDs, keys, intervals or the physical grading rule. The correct route of each focal exhibit depends on both map settings. The first public brief does not necessarily win the lottery.

Every role view and every accepted action response passes through the channel mask. Only loader views in the `dispatch` and `done` phases contain filed records. The control snapshot and the verifier use the complete, unmasked case and events.

In the offline development control, the loader nominates the first public brief, the registrar files its 2 current records and the depot files the 2 agenda records. These accepted action IDs and the depot agenda are identical in all 4 map settings **under those choices**. Other filings can change the agenda. A masked record still has an ID and a key. The reference loader treats a withheld destination as unknown and chooses a legal bay; this does not guarantee reward.

Use the `examples/seatlab/joint_handoffs` author tools in the fork checkout for qualification and native input preparation. The retained stock protocol files describe the source task. Its prior enrollments and archive receipts do not certify this variant. Keep the map settings, channel setting, case, output locks and frozen control cut outside the participants' contexts.
