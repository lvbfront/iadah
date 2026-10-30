# Ablation: hybrid, warm (seed runbooks), scenarios 1–6, n=2

One component switched off at a time (`benchmarks/run.py --ablate`). **false recoveries** = the agent declared recovery but the system was not healthy 10s later.

| config | runs | correct & recovered | recovered | false recoveries | MTTR s | tokens / run | cost / run $ | escalations | unnecessary / inc | errors |
|---|---|---|---|---|---|---|---|---|---|---|
| full hybrid | 12 | 100% | 100% | 0 | 75.5 | 5194 | 0.0116 | 0.58 | 0.08 | 0 |
| (a) no topology guard | 12 | 75% | 83% | 1 | 113.3 | 9566 | 0.0273 | 0.92 | 0.25 | 0 |
| (b) no log compression | 12 | 100% | 100% | 0 | 109.5 | 12611 | 0.0149 | 0.33 | 0.17 | 0 |
| (c) no fast path | 12 | 100% | 100% | 0 | 72.0 | 6199 | 0.0106 | 0.67 | 0.0 | 0 |
| (d) single-poll verify | 12 | 100% | 100% | 0 | 41.9 | 4874 | 0.0101 | 0.67 | 0.0 | 0 |

## Correct & recovered by scenario

| config | s1 | s2 | s3 | s4 | s5 | s6 |
|---|---|---|---|---|---|---|
| full hybrid | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 |
| (a) no topology guard | 2/2 | 0/2 | 2/2 | 2/2 | 2/2 | 1/2 |
| (b) no log compression | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 |
| (c) no fast path | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 |
| (d) single-poll verify | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 |
