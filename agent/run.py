"""Run one recovery pass: uv run python -m agent.run"""
import time

import config
from adapters.docker_adapter import DockerAdapter
from agent import stages
from agent.state import Incident


def run(adapter=None) -> Incident:
    adapter = adapter or DockerAdapter()
    inc = Incident(id=time.strftime("inc-%Y%m%d-%H%M%S"))
    stages.detect(inc, adapter)
    print(f"[detect] unhealthy={list(inc.unhealthy)} suspects={inc.suspects} symptoms={inc.symptoms}")
    if not inc.unhealthy:
        print("system healthy, nothing to do")
        return inc
    stages.compress_logs(inc, adapter)
    stages.triage(inc, adapter)
    while inc.iteration < config.MAX_VERIFY_ITERATIONS:
        inc.iteration += 1
        for stage in (stages.investigate, stages.diagnose, stages.plan, stages.act, stages.verify):
            inc.stage = stage.__name__
            stage(inc, adapter)
            print(f"[{inc.iteration}] {inc.stage}")
        if inc.resolved:
            break
    stages.postmortem(inc, adapter)
    print(f"resolved={inc.resolved} after {inc.iteration} iteration(s)")
    return inc


if __name__ == "__main__":
    run()
