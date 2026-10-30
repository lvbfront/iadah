# Benchmark results

12 valid runs (0 discarded: machine slept > 60s mid-run, rerun). Ground truth = `chaos.TRUTH` (scenario 6 is held out, graded on root service). **diag accuracy** grades the agent's final roots (model output minus topology symptoms); **model-raw diag** grades the model's root_causes before any rule filtering. MTTR averages resolved runs only. **correct & recovered** = right diagnosis AND verified recovery AND still healthy 10s later. **unnecessary** = an action on a service that is not a true root, or a repeated action. cold = no runbooks, warm = seed runbooks (none for scenarios 5 and 6).

## Fault scenarios 1–6, by mode × condition

| mode | condition | runs | diag accuracy | model-raw diag | recovered | correct & recovered | MTTR s | actions/inc | unnecessary/inc | escalations | cost/run $ | total $ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| hybrid | warm | 12 | 83% | 83% | 83% | 75% | 113.3 | 1.42 | 0.25 | 0.92 | 0.0273 | 0.328 |

## Scenario 0: no-fault control

| mode | runs | false detections | actions taken |
|---|---|---|---|

## By scenario

| scenario | mode | condition | runs | diag accuracy | model-raw diag | recovered | correct & recovered | MTTR s | actions/inc | unnecessary/inc | escalations | cost/run $ | total $ | MTTD s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | hybrid | warm | 2 | 100% | 100% | 100% | 100% | 157.9 | 1.5 | 0.5 | 1.5 | 0.0353 | 0.071 | 0.6 |
| 2 | hybrid | warm | 2 | 0% | 0% | 0% | 0% |  | 0.0 | 0.0 | 2.0 | 0.0542 | 0.108 | 3.5 |
| 3 | hybrid | warm | 2 | 100% | 100% | 100% | 100% | 68.8 | 2.0 | 0.0 | 0.0 | 0.0 | 0.0 | 21.7 |
| 4 | hybrid | warm | 2 | 100% | 100% | 100% | 100% | 63.8 | 1.0 | 0.0 | 0.0 | 0.0 | 0.0 | 17.6 |
| 5 | hybrid | warm | 2 | 100% | 100% | 100% | 100% | 78.2 | 2.0 | 0.0 | 1.0 | 0.0238 | 0.048 | 8.1 |
| 6 | hybrid | warm | 2 | 100% | 100% | 100% | 50% | 198.0 | 2.0 | 1.0 | 1.0 | 0.0505 | 0.101 | 2.3 |

## Where small-only fails (not correct & recovered)

None.

## Invariant violations: 0

## Errors: 0
