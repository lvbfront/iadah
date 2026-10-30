# Benchmark results

12 valid runs (0 discarded: machine slept > 60s mid-run, rerun). Ground truth = `chaos.TRUTH` (scenario 6 is held out, graded on root service). **diag accuracy** grades the agent's final roots (model output minus topology symptoms); **model-raw diag** grades the model's root_causes before any rule filtering. MTTR averages resolved runs only. **correct & recovered** = right diagnosis AND verified recovery AND still healthy 10s later. **unnecessary** = an action on a service that is not a true root, or a repeated action. cold = no runbooks, warm = seed runbooks (none for scenarios 5 and 6).

## Fault scenarios 1–6, by mode × condition

| mode | condition | runs | diag accuracy | model-raw diag | recovered | correct & recovered | MTTR s | actions/inc | unnecessary/inc | escalations | cost/run $ | total $ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| hybrid | warm | 12 | 100% | 92% | 100% | 100% | 41.9 | 1.33 | 0.0 | 0.67 | 0.0101 | 0.121 |

## Scenario 0: no-fault control

| mode | runs | false detections | actions taken |
|---|---|---|---|

## By scenario

| scenario | mode | condition | runs | diag accuracy | model-raw diag | recovered | correct & recovered | MTTR s | actions/inc | unnecessary/inc | escalations | cost/run $ | total $ | MTTD s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | hybrid | warm | 2 | 100% | 50% | 100% | 100% | 23.5 | 1.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.5 |
| 2 | hybrid | warm | 2 | 100% | 100% | 100% | 100% | 39.0 | 1.0 | 0.0 | 1.0 | 0.0073 | 0.015 | 4.2 |
| 3 | hybrid | warm | 2 | 100% | 100% | 100% | 100% | 43.9 | 2.0 | 0.0 | 0.0 | 0.0 | 0.0 | 21.9 |
| 4 | hybrid | warm | 2 | 100% | 100% | 100% | 100% | 43.2 | 1.0 | 0.0 | 0.0 | 0.0 | 0.0 | 18.1 |
| 5 | hybrid | warm | 2 | 100% | 100% | 100% | 100% | 50.5 | 2.0 | 0.0 | 1.0 | 0.0222 | 0.044 | 7.0 |
| 6 | hybrid | warm | 2 | 100% | 100% | 100% | 100% | 51.2 | 1.0 | 0.0 | 2.0 | 0.0311 | 0.062 | 2.0 |

## Where small-only fails (not correct & recovered)

None.

## Invariant violations: 0

## Errors: 0
