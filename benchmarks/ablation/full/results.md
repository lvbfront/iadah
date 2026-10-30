# Benchmark results

12 valid runs (0 discarded: machine slept > 60s mid-run, rerun). Ground truth = `chaos.TRUTH` (scenario 6 is held out, graded on root service). **diag accuracy** grades the agent's final roots (model output minus topology symptoms); **model-raw diag** grades the model's root_causes before any rule filtering. MTTR averages resolved runs only. **correct & recovered** = right diagnosis AND verified recovery AND still healthy 10s later. **unnecessary** = an action on a service that is not a true root, or a repeated action. cold = no runbooks, warm = seed runbooks (none for scenarios 5 and 6).

## Fault scenarios 1–6, by mode × condition

| mode | condition | runs | diag accuracy | model-raw diag | recovered | correct & recovered | MTTR s | actions/inc | unnecessary/inc | escalations | cost/run $ | total $ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| hybrid | warm | 12 | 100% | 83% | 100% | 100% | 75.5 | 1.42 | 0.08 | 0.58 | 0.0116 | 0.139 |

## Scenario 0: no-fault control

| mode | runs | false detections | actions taken |
|---|---|---|---|

## By scenario

| scenario | mode | condition | runs | diag accuracy | model-raw diag | recovered | correct & recovered | MTTR s | actions/inc | unnecessary/inc | escalations | cost/run $ | total $ | MTTD s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | hybrid | warm | 2 | 100% | 0% | 100% | 100% | 44.8 | 1.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.5 |
| 2 | hybrid | warm | 2 | 100% | 100% | 100% | 100% | 61.5 | 1.0 | 0.0 | 1.0 | 0.0075 | 0.015 | 4.0 |
| 3 | hybrid | warm | 2 | 100% | 100% | 100% | 100% | 66.7 | 2.0 | 0.0 | 0.0 | 0.0 | 0.0 | 21.9 |
| 4 | hybrid | warm | 2 | 100% | 100% | 100% | 100% | 62.8 | 1.0 | 0.0 | 0.0 | 0.0 | 0.0 | 17.6 |
| 5 | hybrid | warm | 2 | 100% | 100% | 100% | 100% | 80.3 | 2.0 | 0.0 | 1.0 | 0.0236 | 0.047 | 7.0 |
| 6 | hybrid | warm | 2 | 100% | 100% | 100% | 100% | 137.1 | 1.5 | 0.5 | 1.5 | 0.0385 | 0.077 | 1.9 |

## Where small-only fails (not correct & recovered)

None.

## Invariant violations: 0

## Errors: 0
