import json
import time
import urllib.request

import docker

import config
from adapters.base import Actions, Observability

PROJECT_LABEL = "com.docker.compose.project"
SERVICE_LABEL = "com.docker.compose.service"
DEPENDS_LABEL = "recovery.depends_on"  # set in demo/docker-compose.yml


class DockerAdapter(Observability, Actions):
    def __init__(self, project: str = config.TARGET_PROJECT):
        self.client = docker.from_env()
        self.project = project

    def _containers(self):
        return self.client.containers.list(all=True, filters={"label": f"{PROJECT_LABEL}={self.project}"})

    def _container(self, service: str):
        for c in self._containers():
            if c.labels.get(SERVICE_LABEL) == service:
                return c
        raise KeyError(f"no container for service {service!r}")

    # --- observability ---

    def get_logs(self, service, tail=200, since_s=None):
        kw = {"tail": tail, "timestamps": True}
        if since_s:
            kw["since"] = int(time.time() - since_s)
        return self._container(service).logs(**kw).decode(errors="replace").splitlines()

    def get_metrics(self, service):
        c = self._container(service)
        out = {"service": service, "status": c.status}
        if c.status == "running":
            s = c.stats(stream=False)
            mem = s.get("memory_stats", {})
            cpu, pre = s["cpu_stats"], s["precpu_stats"]
            cpu_delta = cpu["cpu_usage"]["total_usage"] - pre.get("cpu_usage", {}).get("total_usage", 0)
            sys_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
            out |= {
                "mem_mb": round(mem.get("usage", 0) / 2**20, 1),
                "mem_limit_mb": round(mem.get("limit", 0) / 2**20, 1),
                "cpu_pct": round(cpu_delta / sys_delta * cpu.get("online_cpus", 1) * 100, 1) if sys_delta > 0 else 0.0,
                "restarts": c.attrs["RestartCount"],
            }
        if service == "api":
            try:
                with urllib.request.urlopen(f"{config.API_URL}/metrics", timeout=2) as r:
                    out["app"] = json.load(r)
            except Exception as e:
                out["app_error"] = str(e)
        return out

    def get_health(self):
        health = {}
        for c in self._containers():
            svc = c.labels.get(SERVICE_LABEL)
            if svc is None:
                continue
            hc = c.attrs["State"].get("Health", {}).get("Status")  # healthy/unhealthy/starting/None
            status = "down" if c.status != "running" else (hc or "running")
            health[svc] = {"status": status, "container": c.name, "state": c.status}
        return health

    def get_topology(self):
        return {c.labels[SERVICE_LABEL]: [d for d in c.labels.get(DEPENDS_LABEL, "").split(",") if d]
                for c in self._containers() if SERVICE_LABEL in c.labels}

    # --- actions (only restart wired tonight) ---

    def restart(self, service):
        c = self._container(service)
        c.restart(timeout=10) if c.status == "running" else c.start()
        return f"restarted {service}"

    def rollback_config(self, service):
        raise NotImplementedError

    def scale(self, service, replicas):
        raise NotImplementedError

    def clear_disk(self, service, path):
        raise NotImplementedError


if __name__ == "__main__":
    a = DockerAdapter()
    print("topology:", a.get_topology())
    print("health:", json.dumps(a.get_health(), indent=1))
    print("metrics(api):", a.get_metrics("api"))
    print("logs(api):", *a.get_logs("api", tail=5), sep="\n  ")
