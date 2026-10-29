import io
import json
import tarfile
import time
import urllib.request
from pathlib import Path

import docker

import config
from adapters.base import Actions, Observability

PROJECT_LABEL = "com.docker.compose.project"
SERVICE_LABEL = "com.docker.compose.service"
# Set in demo/docker-compose.yml:
DEPENDS_LABEL = "recovery.depends_on"
METRICS_LABEL = "recovery.metrics_url"  # JSON metrics endpoint, reachable from where the agent runs
CONFIG_LABEL = "recovery.config_path"   # config file inside the container (rollback_config target)
KILL_IDLE_LABEL = "recovery.kill_idle_cmd"  # shell command inside a DB container that kills idle clients


class DockerAdapter(Observability, Actions):
    def __init__(self, project: str = config.TARGET_PROJECT):
        self.client = docker.from_env()
        self.project = project
        self.lkg_dir = Path(config.STATE_DIR) / "lkg" / project

    def _containers(self):
        return self.client.containers.list(all=True, filters={"label": f"{PROJECT_LABEL}={self.project}"})

    def _container(self, service: str):
        for c in self._containers():
            if c.labels.get(SERVICE_LABEL) == service:
                return c
        raise KeyError(f"no container for service {service!r}")

    @staticmethod
    def _app_metrics(c) -> dict | None:
        url = c.labels.get(METRICS_LABEL)
        if not url or c.status != "running":
            return None
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                return json.load(r)
        except Exception as e:
            return {"error": str(e)}

    # --- observability ---

    def get_logs(self, service, tail=200, since_s=None):
        kw = {"tail": tail, "timestamps": True}
        if since_s:
            kw["since"] = int(time.time() - since_s)
        return self._container(service).logs(**kw).decode(errors="replace").splitlines()

    def get_metrics(self, service):
        c = self._container(service)
        out = {"service": service, "status": c.status, "restarts": c.attrs["RestartCount"]}
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
            }
        if (app := self._app_metrics(c)) is not None:
            out["app"] = app
        return out

    def get_health(self):
        health = {}
        for c in self._containers():
            svc = c.labels.get(SERVICE_LABEL)
            if svc is None:
                continue
            hc = c.attrs["State"].get("Health", {}).get("Status")  # healthy/unhealthy/starting/None
            h = {"status": "down" if c.status != "running" else (hc or "running"), "state": c.status}
            if (app := self._app_metrics(c)) is not None:
                h["app"] = {k: app[k] for k in ("error_rate", "avg_latency_ms", "window_requests", "error") if k in app}
            health[svc] = h
        return health

    def get_topology(self):
        return {c.labels[SERVICE_LABEL]: [d for d in c.labels.get(DEPENDS_LABEL, "").split(",") if d]
                for c in self._containers() if SERVICE_LABEL in c.labels}

    # --- config files (via the archive API, works on stopped containers too) ---

    def _read_config(self, c) -> str:
        stream, _ = c.get_archive(c.labels[CONFIG_LABEL])
        with tarfile.open(fileobj=io.BytesIO(b"".join(stream))) as t:
            return t.extractfile(t.getmembers()[0]).read().decode()

    def _write_config(self, c, text: str):
        path = Path(c.labels[CONFIG_LABEL])
        buf, data = io.BytesIO(), text.encode()
        with tarfile.open(fileobj=buf, mode="w") as t:
            info = tarfile.TarInfo(path.name)
            info.size, info.mode, info.mtime = len(data), 0o644, int(time.time())
            t.addfile(info, io.BytesIO(data))
        c.put_archive(str(path.parent), buf.getvalue())

    # --- actions ---

    def restart(self, service):
        c = self._container(service)
        c.restart(timeout=10) if c.status == "running" else c.start()
        return {"action": "restart", "service": service, "detail": f"restarted {service}", "undo": None}

    def mark_known_good(self, service):
        c = self._container(service)
        if CONFIG_LABEL in c.labels:
            self.lkg_dir.mkdir(parents=True, exist_ok=True)
            (self.lkg_dir / service).write_text(self._read_config(c))

    def rollback_config(self, service):
        c = self._container(service)
        lkg = self.lkg_dir / service
        if CONFIG_LABEL not in c.labels or not lkg.exists():
            raise RuntimeError(f"no last-known-good config for {service}")
        current, good = self._read_config(c), lkg.read_text()
        if current == good:
            raise RuntimeError(f"{service} config already matches last-known-good")
        changed = [l.split("=")[0] for l in set(current.splitlines()) ^ set(good.splitlines()) if "=" in l]
        self._write_config(c, good)
        c.restart(timeout=10)
        return {"action": "rollback_config", "service": service,
                "detail": f"restored last-known-good config (changed keys: {sorted(set(changed))}) and restarted",
                "undo": {"config": current}}

    def undo(self, record):
        if not record.get("undo"):
            return {"action": "undo", "service": record["service"], "detail": "nothing to undo", "undo": None}
        c = self._container(record["service"])
        self._write_config(c, record["undo"]["config"])
        c.restart(timeout=10)
        return {"action": "undo", "service": record["service"],
                "detail": f"reverted {record['action']}", "undo": None}

    def kill_idle_connections(self, service):
        c = self._container(service)
        if KILL_IDLE_LABEL not in c.labels:
            raise RuntimeError(f"{service} has no {KILL_IDLE_LABEL} hook (not a DB?)")
        code, out = c.exec_run(["sh", "-c", c.labels[KILL_IDLE_LABEL]])
        if code != 0:
            raise RuntimeError(out.decode(errors="replace").strip())
        return {"action": "kill_idle_connections", "service": service,
                "detail": f"terminated {out.decode().strip()} idle connections on {service}", "undo": None}

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
