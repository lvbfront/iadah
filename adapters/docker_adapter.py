import io
import json
import subprocess
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
SCRATCH_LABEL = "recovery.scratch_path"     # the only directory clear_disk may empty
STATUS_RANK = {"healthy": 0, "running": 1, "starting": 2, "unhealthy": 3, "down": 4}


class DockerAdapter(Observability, Actions):
    def __init__(self, project: str = config.TARGET_PROJECT):
        self.client = docker.from_env()
        self.project = project
        self.lkg_dir = Path(config.STATE_DIR) / "lkg" / project

    def _containers(self):
        return self.client.containers.list(all=True, filters={"label": f"{PROJECT_LABEL}={self.project}"})

    def _replicas(self, service: str):
        cs = sorted((c for c in self._containers() if c.labels.get(SERVICE_LABEL) == service), key=lambda c: c.name)
        if not cs:
            raise KeyError(f"no container for service {service!r}")
        return cs

    def _container(self, service: str):
        return self._replicas(service)[0]

    @staticmethod
    def _mem_pct(c) -> float | None:
        if c.status != "running":
            return None
        m = c.stats(stream=False, one_shot=True).get("memory_stats", {})
        if not m.get("limit"):
            return None
        used = m.get("usage", 0) - m.get("stats", {}).get("inactive_file", 0)
        return round(100 * used / m["limit"], 1)

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
        out = {"service": service, "status": c.status, "restarts": c.attrs["RestartCount"],
               "oom_killed": c.attrs["State"].get("OOMKilled", False), "replicas": len(self._replicas(service))}
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
        if SCRATCH_LABEL in c.labels and c.status == "running":
            code, df = c.exec_run(["df", "-k", c.labels[SCRATCH_LABEL]])
            if code == 0:
                size, used = df.decode().splitlines()[-1].split()[1:3]
                out["disk"] = {"path": c.labels[SCRATCH_LABEL], "used_pct": round(100 * int(used) / int(size), 1)}
        return out

    def get_health(self):
        """Per service; with several replicas, the worst replica wins (status, memory)."""
        health = {}
        for c in self._containers():
            svc = c.labels.get(SERVICE_LABEL)
            if svc is None:
                continue
            hc = c.attrs["State"].get("Health", {}).get("Status")  # healthy/unhealthy/starting/None
            h = {"status": "down" if c.status != "running" else (hc or "running"), "state": c.status,
                 "mem_pct": self._mem_pct(c)}
            if (app := self._app_metrics(c)) is not None:
                h["app"] = {k: app[k] for k in ("error_rate", "avg_latency_ms", "window_requests", "error") if k in app}
            prev = health.get(svc)
            if prev:
                h["replicas"] = prev.get("replicas", 1) + 1
                h["mem_pct"] = max(filter(None, [h["mem_pct"], prev["mem_pct"]]), default=None)
                if STATUS_RANK[prev["status"]] > STATUS_RANK[h["status"]]:
                    h["status"], h["state"] = prev["status"], prev["state"]
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
        cs = self._replicas(service)
        for c in cs:
            c.restart(timeout=10) if c.status == "running" else c.start()
        return {"action": "restart", "service": service, "detail": f"restarted {service} ({len(cs)} replica(s))",
                "undo": None}

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
        if record["action"] == "scale":
            self.scale(record["service"], record["undo"]["replicas"])
            return {"action": "undo", "service": record["service"], "detail": "reverted scale", "undo": None}
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
        replicas = max(1, min(int(replicas), 5))  # ponytail: hard cap so a bad plan can't eat the laptop
        c = self._container(service)
        before = len(self._replicas(service))
        files = c.labels["com.docker.compose.project.config_files"].split(",")
        subprocess.run(["docker", "compose", "-p", self.project, *sum((["-f", f] for f in files), []),
                        "up", "-d", "--no-recreate", "--no-deps", "--scale", f"{service}={replicas}", service],
                       check=True, capture_output=True)
        return {"action": "scale", "service": service, "detail": f"scaled {service} {before} → {replicas} replicas",
                "undo": {"replicas": before}}

    def clear_disk(self, service):
        c = self._container(service)
        path = c.labels.get(SCRATCH_LABEL)
        if not path:
            raise RuntimeError(f"{service} has no {SCRATCH_LABEL}; refusing to delete anything")
        code, out = c.exec_run(["sh", "-c", f'du -sk "{path}" | cut -f1; find "{path}" -mindepth 1 -delete'])
        if code != 0:
            raise RuntimeError(out.decode(errors="replace").strip())
        return {"action": "clear_disk", "service": service,
                "detail": f"freed {int(out.decode().split()[0]) // 1024} MB under {path} on {service}", "undo": None}


if __name__ == "__main__":
    a = DockerAdapter()
    print("topology:", a.get_topology())
    print("health:", json.dumps(a.get_health(), indent=1))
    print("metrics(api):", a.get_metrics("api"))
    print("logs(api):", *a.get_logs("api", tail=5), sep="\n  ")
