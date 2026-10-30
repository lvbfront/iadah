# Benchmark results

36 valid runs (6 discarded: machine slept > 60s mid-run, rerun). Ground truth = `chaos.TRUTH` (scenario 6 is held out, graded on root service). **diag accuracy** grades the agent's final roots (model output minus topology symptoms); **model-raw diag** grades the model's root_causes before any rule filtering. MTTR averages resolved runs only. **correct & recovered** = right diagnosis AND verified recovery AND still healthy 10s later. **unnecessary** = an action on a service that is not a true root, or a repeated action. cold = no runbooks, warm = seed runbooks (none for scenarios 5 and 6).

## Fault scenarios 1–6, by mode × condition

| mode | condition | runs | diag accuracy | model-raw diag | recovered | correct & recovered | MTTR s | actions/inc | unnecessary/inc | escalations | cost/run $ | total $ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| hybrid | cold | 6 | 0% | 0% | 0% | 0% |  | 0.5 | 0.5 | 0.67 | 0.0099 | 0.059 |
| hybrid | warm | 6 | 0% | 0% | 0% | 0% |  | 0.5 | 0.5 | 0.67 | 0.0157 | 0.094 |
| big-only | cold | 6 | 0% | 0% | 0% | 0% |  | 0.5 | 0.5 | 0.0 | 0.0221 | 0.133 |
| big-only | warm | 6 | 0% | 0% | 0% | 0% |  | 0.5 | 0.5 | 0.0 | 0.0279 | 0.167 |
| small-only | cold | 6 | 0% | 0% | 0% | 0% |  | 0.33 | 0.33 | 0.0 | 0.0 | 0.0 |
| small-only | warm | 6 | 0% | 0% | 0% | 0% |  | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |

## Scenario 0: no-fault control

| mode | runs | false detections | actions taken |
|---|---|---|---|

## By scenario

| scenario | mode | condition | runs | diag accuracy | model-raw diag | recovered | correct & recovered | MTTR s | actions/inc | unnecessary/inc | escalations | cost/run $ | total $ | MTTD s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 7 | hybrid | cold | 3 | 0% | 0% | 0% | 0% |  | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |  |
| 7 | hybrid | warm | 3 | 0% | 0% | 0% | 0% |  | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |  |
| 7 | big-only | cold | 3 | 0% | 0% | 0% | 0% |  | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |  |
| 7 | big-only | warm | 3 | 0% | 0% | 0% | 0% |  | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |  |
| 7 | small-only | cold | 3 | 0% | 0% | 0% | 0% |  | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |  |
| 7 | small-only | warm | 3 | 0% | 0% | 0% | 0% |  | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |  |
| 8 | hybrid | cold | 3 | 0% | 0% | 0% | 0% |  | 1.0 | 1.0 | 1.33 | 0.0197 | 0.059 | 3.4 |
| 8 | hybrid | warm | 3 | 0% | 0% | 0% | 0% |  | 1.0 | 1.0 | 1.33 | 0.0313 | 0.094 | 3.7 |
| 8 | big-only | cold | 3 | 0% | 0% | 0% | 0% |  | 1.0 | 1.0 | 0.0 | 0.0442 | 0.133 | 3.4 |
| 8 | big-only | warm | 3 | 0% | 0% | 0% | 0% |  | 1.0 | 1.0 | 0.0 | 0.0558 | 0.167 | 3.4 |
| 8 | small-only | cold | 3 | 0% | 0% | 0% | 0% |  | 0.67 | 0.67 | 0.0 | 0.0 | 0.0 | 3.4 |
| 8 | small-only | warm | 3 | 0% | 0% | 0% | 0% |  | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 3.5 |

## Where small-only fails (not correct & recovered)

| run | diagnosed | truth | resolved | actions | error |
|---|---|---|---|---|---|
| s7-small-only-cold-1 | none | *@worker | 0 |  |  |
| s7-small-only-warm-1 | none | *@worker | 0 |  |  |
| s8-small-only-cold-1 | none | *@db | 0 |  |  |
| s8-small-only-warm-1 | none | *@db | 0 |  |  |
| s7-small-only-cold-2 | none | *@worker | 0 |  |  |
| s7-small-only-warm-2 | none | *@worker | 0 |  |  |
| s8-small-only-cold-2 | none | *@db | 0 |  |  |
| s8-small-only-warm-2 | none | *@db | 0 |  |  |
| s7-small-only-cold-3 | none | *@worker | 0 |  |  |
| s7-small-only-warm-3 | none | *@worker | 0 |  |  |
| s8-small-only-cold-3 | db_pool_exhaustion@api | *@db | 0 | scale(db);restart(api) |  |
| s8-small-only-warm-3 | none | *@db | 0 |  |  |

## Invariant violations: 0

## Errors: 0
