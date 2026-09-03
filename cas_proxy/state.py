from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .config import ServiceConfig


@dataclass
class ServiceStats:
    name: str
    kind: str
    listeners: list[str]
    required: bool
    state: str = "starting"
    upstream_connected: bool | None = None
    active_clients: int = 0
    total_connections: int = 0
    bytes_from_upstream: int = 0
    bytes_to_upstream: int = 0
    bytes_from_clients: int = 0
    bytes_to_clients: int = 0
    reconnects: int = 0
    errors: int = 0
    dropped_clients: int = 0
    last_error: str | None = None
    changed_at: float = field(default_factory=time.time)

    @classmethod
    def from_config(cls, config: ServiceConfig) -> "ServiceStats":
        return cls(
            name=config.name,
            kind=config.kind,
            listeners=[str(endpoint) for endpoint in config.listen],
            required=config.required,
            upstream_connected=False if config.kind == "rpm_broadcast" else None,
        )

    def set_state(self, state: str, error: Exception | str | None = None) -> None:
        self.state = state
        self.changed_at = time.time()
        if error is not None:
            self.errors += 1
            self.last_error = str(error)[:500]

    def snapshot(self) -> dict[str, Any]:
        result = asdict(self)
        result["changed_at"] = round(self.changed_at, 3)
        return result


class Registry:
    def __init__(self) -> None:
        self.started_at = time.time()
        self.services: dict[str, ServiceStats] = {}

    def register(self, config: ServiceConfig) -> ServiceStats:
        stats = ServiceStats.from_config(config)
        self.services[config.name] = stats
        return stats

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.ready() else "degraded",
            "uptime_seconds": round(time.time() - self.started_at, 3),
            "services": [stats.snapshot() for stats in self.services.values()],
        }

    def ready(self) -> bool:
        for stats in self.services.values():
            if not stats.required:
                continue
            if stats.state not in {"running", "connected"}:
                return False
            if stats.kind == "rpm_broadcast" and not stats.upstream_connected:
                return False
        return True

    def metrics(self) -> str:
        lines = [
            "# HELP cas_proxy_uptime_seconds Process uptime in seconds.",
            "# TYPE cas_proxy_uptime_seconds gauge",
            f"cas_proxy_uptime_seconds {time.time() - self.started_at:.3f}",
        ]
        numeric_fields = (
            "active_clients",
            "total_connections",
            "bytes_from_upstream",
            "bytes_to_upstream",
            "bytes_from_clients",
            "bytes_to_clients",
            "reconnects",
            "errors",
            "dropped_clients",
        )
        for stats in self.services.values():
            labels = f'name="{stats.name}",kind="{stats.kind}"'
            lines.append(f"cas_proxy_service_up{{{labels}}} {1 if stats.state in {'running', 'connected'} else 0}")
            if stats.upstream_connected is not None:
                lines.append(f"cas_proxy_upstream_connected{{{labels}}} {1 if stats.upstream_connected else 0}")
            for field_name in numeric_fields:
                lines.append(f"cas_proxy_{field_name}{{{labels}}} {getattr(stats, field_name)}")
        return "\n".join(lines) + "\n"
