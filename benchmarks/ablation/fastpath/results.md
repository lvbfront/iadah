# Benchmark results

12 valid runs (0 discarded: machine slept > 60s mid-run, rerun). Ground truth = `chaos.TRUTH` (scenario 6 is held out, graded on root service). **diag accuracy** grades the agent's final roots (model output minus topology symptoms); **model-raw diag** grades the model's root_causes before any rule filtering. MTTR averages resolved runs only. **correct & recovered** = right diagnosis AND verified recovery AND still healthy 10s later. **unnecessary** = an action on a service that is not a true root, or a repeated action. cold = no runbooks, warm = seed runbooks (none for scenarios 5 and 6).

## Fault scenarios 1–6, by mode × condition

| mode | condition | runs | diag accuracy | model-raw diag | recovered | correct & recovered | MTTR s | actions/inc | unnecessary/inc | escalations | cost/run $ | total $ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| hybrid | warm | 12 | 100% | 100% | 100% | 100% | 72.0 | 1.33 | 0.0 | 0.67 | 0.0106 | 0.127 |

## Scenario 0: no-fault control

| mode | runs | false detections | actions taken |
|---|---|---|---|

## By scenario

| scenario | mode | condition | runs | diag accuracy | model-raw diag | recovered | correct & recovered | MTTR s | actions/inc | unnecessary/inc | escalations | cost/run $ | total $ | MTTD s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | hybrid | warm | 2 | 100% | 100% | 100% | 100% | 55.1 | 1.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.4 |
| 2 | hybrid | warm | 2 | 100% | 100% | 100% | 100% | 71.7 | 1.0 | 0.0 | 1.0 | 0.0075 | 0.015 | 3.5 |
| 3 | hybrid | warm | 2 | 100% | 100% | 100% | 100% | 81.5 | 2.0 | 0.0 | 0.0 | 0.0 | 0.0 | 22.0 |
| 4 | hybrid | warm | 2 | 100% | 100% | 100% | 100% | 78.2 | 1.0 | 0.0 | 0.0 | 0.0 | 0.0 | 18.0 |
| 5 | hybrid | warm | 2 | 100% | 100% | 100% | 100% | 73.6 | 2.0 | 0.0 | 1.0 | 0.0229 | 0.046 | 7.4 |
| 6 | hybrid | warm | 2 | 100% | 100% | 100% | 100% | 71.8 | 1.0 | 0.0 | 2.0 | 0.0331 | 0.066 | 1.9 |

## Where small-only fails (not correct & recovered)

None.

## Invariant violations: 0

## Errors: 0
