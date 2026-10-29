"""Break the agent on purpose and check it fails safe (escalates, undoes, or hands to a human).

uv run python -m tests.failsafe            # all cases (~10 min, uses the demo stack)
uv run python -m tests.failsafe 2 5        # selected cases

Each case patches one thing, runs a real scenario, then asserts: no exception escapes, no invariant
violation, only allowlisted actions on plausible targets, and the case-specific expectation.
"""
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from unittest import mock

import yaml

import chaos
import config
import telemetry
from adapters.docker_adapter import DockerAdapter
from agent import schemas
from agent.run import run
from llm import backends


class NoTopology(DockerAdapter):
    def get_topology(self):
        return {s: [] for s in super().get_topology()}


class ScaleExplodes(DockerAdapter):
    def scale(self, service, replicas):
        raise RuntimeError("simulated: scale API failed")


def boom(*a, **k):
    raise RuntimeError("simulated: connection refused / timeout")


def case_ollama_down():
    with mock.patch.object(config, "OLLAMA_URL", "http://127.0.0.1:1"):
        backends._backends.clear()
        inc = run("1", DockerAdapter())
    backends._backends.clear()
    return inc, "small model unreachable → escalates to Claude, recovers", \
        lambda i: i.resolved and i.escalations >= 1 and all(a["service"] == "cache" for a in i.actions_taken)


def case_claude_down():
    with mock.patch.object(backends.AnthropicBackend, "complete", boom):
        inc = run("5", DockerAdapter())  # 2 root causes → must escalate → Claude is down
    return inc, "Claude unreachable on a case that needs it → hands to a human, takes NO action", \
        lambda i: bool(i.needs_human) and not i.actions_taken


def case_malformed_json():
    bad = backends.LLMResult('{"incident_type": "cache_down", oops', "qwen3.5:4b", "qwen3.5:4b", "small", 10, 5, 0.1, 0)
    with mock.patch.object(backends.OllamaBackend, "complete", lambda *a, **k: bad), \
            mock.patch.object(config, "MODE", "small-only"):
        inc = run("2", DockerAdapter())  # small-only: nowhere to escalate
    return inc, "small model returns broken JSON in small-only mode → hands to a human, takes NO action", \
        lambda i: bool(i.needs_human) and not i.actions_taken


def case_no_topology():
    inc = run("5", NoTopology())
    return inc, "topology labels missing → flagged, treated as ambiguous (no fast path); no unapproved action", \
        lambda i: i.topology_missing and not i.fast_path


def case_action_throws():
    inc = run("3", ScaleExplodes())  # memory_leak runbook: restart(worker) then scale(worker) → scale throws
    return inc, "action throws mid-plan → earlier actions of that plan reverted, no crash", \
        lambda i: any(r["action"] == "restart" for r in i.undone)


CASES = {"1": case_ollama_down, "2": case_claude_down, "3": case_malformed_json,
         "4": case_no_topology, "5": case_action_throws}


def main(which):
    config.AUTO_APPROVE = True
    config.MODE = "hybrid"
    config.TELEMETRY_DB = str(Path(tempfile.mkdtemp()) / "failsafe.db")
    results = []
    for k in which:
        rb = Path(tempfile.mkdtemp(prefix="runbooks-failsafe-"))
        for p in Path("runbooks").glob("*.yaml"):
            if yaml.safe_load(p.read_text()).get("source") == "seed":
                shutil.copy(p, rb)
        config.RUNBOOKS_DIR = str(rb)
        name = CASES[k].__name__.removeprefix("case_")
        try:
            inc, expect, ok = CASES[k]()
            acts = [f"{a['action']}({a['service']})" for a in inc.actions_taken]
            problems = list(inc.violations)
            problems += [f"non-allowlisted {a}" for a in inc.actions_taken if a["action"] not in schemas.ALLOWLIST]
            if not ok(inc):
                problems.append("expectation not met")
            results.append((k, name, not problems, expect, f"resolved={inc.resolved} needs_human={inc.needs_human!r} "
                                                           f"actions={acts} undone={[u['action'] for u in inc.undone]}",
                            problems))
        except Exception as e:
            traceback.print_exc()
            results.append((k, name, False, "no exception escapes", "", [f"{type(e).__name__}: {e}"]))
        finally:
            shutil.rmtree(rb, ignore_errors=True)
            chaos.reset()
    telemetry.console.rule("fail-safe results")
    for k, name, passed, expect, got, problems in results:
        telemetry.console.print(f"{'[green]PASS' if passed else '[red]FAIL'}[/] {k} {name}: {expect}\n     {got} {problems}")
    return all(r[2] for r in results)


if __name__ == "__main__":
    sys.exit(0 if main(sys.argv[1:] or list(CASES)) else 1)
