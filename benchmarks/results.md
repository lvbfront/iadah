# Benchmark results

2 runs. Ground truth = `chaos.TRUTH`. MTTR averages resolved runs only. cold = no runbooks, warm = seed runbooks (none for scenario 5).

## By mode × condition

| mode | condition | runs | diag accuracy | recovered | MTTR s | escalations | cost/run $ | total $ |
|---|---|---|---|---|---|---|---|---|
| small-only | cold | 1 | 100% | 100% | 65.9 | 0.0 | 0.0 | 0.0 |
| small-only | warm | 1 | 100% | 100% | 51.5 | 0.0 | 0.0 | 0.0 |

## By scenario

| scenario | mode | condition | runs | diag accuracy | recovered | MTTD s | MTTR s | escalations | tokens | big tokens | cost $ |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 4 | small-only | cold | 1 | 100% | 100% | 28.7 | 65.9 | 0.0 | 3931 | 0 | 0.0 |
| 4 | small-only | warm | 1 | 100% | 100% | 28.7 | 51.5 | 0.0 | 1468 | 0 | 0.0 |

## Where small-only fails

No small-only failures.
