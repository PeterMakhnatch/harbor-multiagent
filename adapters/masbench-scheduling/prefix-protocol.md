# Test the first changed action in a live negotiation

## Question

A replay can predict the next action while the recorded history still applies. After the predicted action changes, the original team's later answers need not occur. Test that boundary with a real coordinator policy swap in Olam Harbor. This tests agreement between two implementations, not model ability.

## Registration

Use the 4 development and 20 follow-up cases from the [original interactive trial](https://github.com/PeterMakhnatch/harbor-multiagent/tree/ac8d7eae0a6ebd4f21a39e23d395bbab80bf3c8f/adapters/masbench-scheduling). Keep those groups separate. The existing unbounded choices and round counts are known. No bounded trial has been collected when this plan is written. The 24 new trials each require 5 real participants, a separate world process, and an independent verifier. Do not run a model. Do not substitute a scripted offline answer for a missing native trial.

Keep the private case, participant order, initial coordinator view, proposal format, and 4 other actor policies fixed. Change the coordinator's action policy, not its world inputs. It uses the same cursor until 8 completed rounds. A unanimous answer or exhausted seat takes precedence. If neither applies after round 8, the coordinator submits a real null and ends the negotiation. The world must receive and bind this action. An absent action or verifier result is unmeasured.

## What to compare

Export each native coordinator conversation as an ordered sequence of accepted views and actions. The initial view precedes query 0. An accepted round bundle precedes the following query or terminal choice. The action after the eighth bundle therefore occurs at decision 9. Compare an unbounded replay and a bounded replay on the original trace. Stop executing the policy at the first changed action, unknown action, or unacknowledged observation. Do not send later original views to the alternative policy. Do not assign a downstream reward to the stopped replay.

Independently run the bounded team. Pair the native runs by original case content and role, never by comparing a trial to itself. Check the whole initial view, each subsequent acknowledged view, and every prior action. Record equal prefixes, first changed inputs, first changed actions, and whether the bounded replay predicted that exact boundary action. A changed input before an alleged policy divergence blocks attribution. A missing arm remains in the denominator. Report the actual target choice and score from the target verifier only. Record both UUIDs, all 24 scheduled cases, actual actor executions, and both policies' native resource counts. Outcomes are not independent across repeated conditions on the same case.

## Admission

Run in fresh private directories under the shared local Docker lock. Preserve priority for the registered hourly stability control and the bounded first-party speaker–listener study. Start no more than 1 of our native trials at a time. No external model or paid service is permitted for this comparison. Publish only verified synthetic actor data and safe projections on Peter's fork.
