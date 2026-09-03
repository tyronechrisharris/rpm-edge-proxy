from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when a configuration file is invalid."""


_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int

    @classmethod
    def parse(cls, value: str, field: str) -> "Endpoint":
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{field} must be a non-empty host:port string")
        value = value.strip()
        if value.startswith("["):
            end = value.find("]")
            if end < 0 or end + 1 >= len(value) or value[end + 1] != ":":
                raise ConfigError(f"{field} must use [IPv6]:port syntax")
            host, port_text = value[1:end], value[end + 2 :]
        else:
            if ":" not in value:
                raise ConfigError(f"{field} must include a port")
            host, port_text = value.rsplit(":", 1)
        if not host:
            raise ConfigError(f"{field} host cannot be empty")
        try:
            port = int(port_text)
        except ValueError as exc:
            raise ConfigError(f"{field} has an invalid port: {port_text!r}") from exc
        if not 1 <= port <= 65535:
            raise ConfigError(f"{field} port must be between 1 and 65535")
        return cls(host=host, port=port)

    def __str__(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{host}:{self.port}"


@dataclass(frozen=True)
class Upstream:
    endpoint: Endpoint
    interface: str | None = None
    source_ip: str | None = None


@dataclass(frozen=True)
class ServiceConfig:
    name: str
    kind: str
    listen: tuple[Endpoint, ...]
    upstream: Upstream
    enabled: bool = True
    required: bool = False
    allowed_clients: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = ()
    connect_timeout_seconds: float = 5.0
    reconnect_delay_seconds: float = 5.0
    idle_timeout_seconds: float = 0.0
    udp_session_timeout_seconds: float = 60.0
    client_writes: str = "discard"
    disconnect_clients_on_upstream_loss: bool = True
    queue_packets: int = 256


@dataclass(frozen=True)
class AppConfig:
    status_listen: Endpoint
    log_level: str
    services: tuple[ServiceConfig, ...]


def _expect_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{field} must be a JSON object")
    return value


def _unknown_keys(obj: dict[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(set(obj) - allowed)
    if unknown:
        raise ConfigError(f"{field} contains unknown field(s): {', '.join(unknown)}")


def _positive_number(value: Any, field: str, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{field} must be a number")
    number = float(value)
    if number < 0 or (number == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ConfigError(f"{field} must be {qualifier}")
    return number


def _parse_upstream(value: Any, field: str) -> Upstream:
    obj = _expect_object(value, field)
    _unknown_keys(obj, {"address", "interface", "source_ip"}, field)
    endpoint = Endpoint.parse(obj.get("address"), f"{field}.address")
    interface = obj.get("interface")
    if interface is not None and (not isinstance(interface, str) or not interface.strip()):
        raise ConfigError(f"{field}.interface must be a non-empty string")
    source_ip = obj.get("source_ip")
    if source_ip is not None:
        try:
            ipaddress.ip_address(source_ip)
        except ValueError as exc:
            raise ConfigError(f"{field}.source_ip is not a valid IP address") from exc
    return Upstream(endpoint=endpoint, interface=interface, source_ip=source_ip)


def _parse_networks(value: Any, field: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError(f"{field} must be an array of CIDR strings")
    result = []
    for index, item in enumerate(value):
        try:
            result.append(ipaddress.ip_network(item, strict=False))
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{field}[{index}] is not a valid CIDR network") from exc
    return tuple(result)


def _parse_service(value: Any, index: int) -> ServiceConfig:
    field = f"services[{index}]"
    obj = _expect_object(value, field)
    _unknown_keys(
        obj,
        {
            "name",
            "kind",
            "listen",
            "upstream",
            "enabled",
            "required",
            "allowed_clients",
            "connect_timeout_seconds",
            "reconnect_delay_seconds",
            "idle_timeout_seconds",
            "udp_session_timeout_seconds",
            "client_writes",
            "disconnect_clients_on_upstream_loss",
            "queue_packets",
        },
        field,
    )
    name = obj.get("name")
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        raise ConfigError(f"{field}.name must match {_NAME_RE.pattern}")
    kind = obj.get("kind")
    if kind not in {"tcp", "udp", "rpm_broadcast"}:
        raise ConfigError(f"{field}.kind must be tcp, udp, or rpm_broadcast")
    listens = obj.get("listen")
    if not isinstance(listens, list) or not listens:
        raise ConfigError(f"{field}.listen must be a non-empty array")
    listen = tuple(Endpoint.parse(item, f"{field}.listen[{i}]") for i, item in enumerate(listens))
    upstream = _parse_upstream(obj.get("upstream"), f"{field}.upstream")
    enabled = obj.get("enabled", True)
    required = obj.get("required", False)
    disconnect = obj.get("disconnect_clients_on_upstream_loss", True)
    for key, item in (("enabled", enabled), ("required", required), ("disconnect_clients_on_upstream_loss", disconnect)):
        if not isinstance(item, bool):
            raise ConfigError(f"{field}.{key} must be true or false")
    client_writes = obj.get("client_writes", "discard")
    if client_writes not in {"discard", "forward"}:
        raise ConfigError(f"{field}.client_writes must be discard or forward")
    if kind != "rpm_broadcast" and "client_writes" in obj:
        raise ConfigError(f"{field}.client_writes is only valid for rpm_broadcast")
    queue_packets = obj.get("queue_packets", 256)
    if isinstance(queue_packets, bool) or not isinstance(queue_packets, int) or not 1 <= queue_packets <= 65536:
        raise ConfigError(f"{field}.queue_packets must be an integer from 1 to 65536")
    return ServiceConfig(
        name=name,
        kind=kind,
        listen=listen,
        upstream=upstream,
        enabled=enabled,
        required=required,
        allowed_clients=_parse_networks(obj.get("allowed_clients"), f"{field}.allowed_clients"),
        connect_timeout_seconds=_positive_number(obj.get("connect_timeout_seconds", 5), f"{field}.connect_timeout_seconds"),
        reconnect_delay_seconds=_positive_number(obj.get("reconnect_delay_seconds", 5), f"{field}.reconnect_delay_seconds"),
        idle_timeout_seconds=_positive_number(obj.get("idle_timeout_seconds", 0), f"{field}.idle_timeout_seconds", allow_zero=True),
        udp_session_timeout_seconds=_positive_number(obj.get("udp_session_timeout_seconds", 60), f"{field}.udp_session_timeout_seconds"),
        client_writes=client_writes,
        disconnect_clients_on_upstream_loss=disconnect,
        queue_packets=queue_packets,
    )


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read configuration {config_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in {config_path} at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
    obj = _expect_object(raw, "configuration")
    _unknown_keys(obj, {"version", "log_level", "status", "services"}, "configuration")
    if obj.get("version") != 1:
        raise ConfigError("configuration.version must be 1")
    log_level = obj.get("log_level", "INFO")
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ConfigError("configuration.log_level must be DEBUG, INFO, WARNING, or ERROR")
    status_obj = _expect_object(obj.get("status", {}), "configuration.status")
    _unknown_keys(status_obj, {"listen"}, "configuration.status")
    status_listen = Endpoint.parse(status_obj.get("listen", "0.0.0.0:9090"), "configuration.status.listen")
    services_raw = obj.get("services")
    if not isinstance(services_raw, list) or not services_raw:
        raise ConfigError("configuration.services must be a non-empty array")
    services = tuple(_parse_service(item, i) for i, item in enumerate(services_raw))
    names = [service.name for service in services]
    duplicate_names = sorted({name for name in names if names.count(name) > 1})
    if duplicate_names:
        raise ConfigError(f"service names must be unique: {', '.join(duplicate_names)}")
    listeners: dict[tuple[str, str, int], str] = {}
    for service in services:
        if not service.enabled:
            continue
        protocol = "udp" if service.kind == "udp" else "tcp"
        for endpoint in service.listen:
            key = (protocol, endpoint.host, endpoint.port)
            if key in listeners:
                raise ConfigError(
                    f"listener {protocol}://{endpoint} is used by both {listeners[key]} and {service.name}"
                )
            listeners[key] = service.name
    return AppConfig(status_listen=status_listen, log_level=log_level, services=services)
