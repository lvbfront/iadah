"""Target-system interfaces. The agent only talks to these, never to Docker/K8s directly.
Add K8sAdapter / PrometheusAdapter / ELKAdapter by implementing the same methods."""
from abc import ABC, abstractmethod


class Observability(ABC):
    @abstractmethod
    def get_logs(self, service: str, tail: int = 200, since_s: int | None = None) -> list[str]: ...

    @abstractmethod
    def get_metrics(self, service: str) -> dict: ...

    @abstractmethod
    def get_health(self) -> dict[str, dict]:
        """{service: {"status": "healthy"|"unhealthy"|"down"|"starting"|"running",
                      "app": {"error_rate", "avg_latency_ms", ...} if the service exposes metrics}}"""

    @abstractmethod
    def service_meta(self, service: str) -> dict:
        """{"stateful": bool, "replicas": int}"""

    @abstractmethod
    def get_topology(self) -> dict[str, list[str]]:
        """{service: [services it depends on]}"""


class Actions(ABC):
    """Every action returns a record {"action", "service", "detail", "undo": {...} | None}
    that undo() can reverse."""

    @abstractmethod
    def restart(self, service: str) -> dict: ...

    @abstractmethod
    def rollback_config(self, service: str) -> dict:
        """Restore the service's last-known-good config and restart it."""

    @abstractmethod
    def mark_known_good(self, service: str) -> None:
        """Record the service's current config as last-known-good (call when verified healthy)."""

    @abstractmethod
    def undo(self, record: dict) -> dict: ...

    @abstractmethod
    def kill_idle_connections(self, service: str) -> dict:
        """DB services: terminate leaked idle client connections."""

    @abstractmethod
    def scale(self, service: str, replicas: int) -> dict: ...

    @abstractmethod
    def clear_disk(self, service: str) -> dict:
        """Empty the service's declared scratch directory (never an arbitrary path)."""
