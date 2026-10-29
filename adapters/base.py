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
        """{service: {"status": "healthy"|"unhealthy"|"down"|..., ...}}"""

    @abstractmethod
    def get_topology(self) -> dict[str, list[str]]:
        """{service: [services it depends on]}"""


class Actions(ABC):
    @abstractmethod
    def restart(self, service: str) -> str: ...

    @abstractmethod
    def rollback_config(self, service: str) -> str: ...

    @abstractmethod
    def scale(self, service: str, replicas: int) -> str: ...

    @abstractmethod
    def clear_disk(self, service: str, path: str) -> str: ...
