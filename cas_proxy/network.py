from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import socket
import sys
import time
from dataclasses import dataclass
from typing import Any

from .config import Endpoint, ServiceConfig, Upstream
from .state import ServiceStats


LOGGER = logging.getLogger("cas_proxy.network")
BUFFER_SIZE = 65536
TCP_KEEPALIVE_IDLE_SECONDS = 15
TCP_KEEPALIVE_INTERVAL_SECONDS = 5
TCP_KEEPALIVE_PROBES = 3
TCP_USER_TIMEOUT_MILLISECONDS = 30_000


def _tune_tcp_socket(sock: socket.socket) -> None:
    """Apply low-latency and dead-peer detection settings when supported."""
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    options = (
        ("TCP_KEEPIDLE", TCP_KEEPALIVE_IDLE_SECONDS),
        ("TCP_KEEPINTVL", TCP_KEEPALIVE_INTERVAL_SECONDS),
        ("TCP_KEEPCNT", TCP_KEEPALIVE_PROBES),
        ("TCP_USER_TIMEOUT", TCP_USER_TIMEOUT_MILLISECONDS),
    )
    for name, value in options:
        option = getattr(socket, name, None)
        if option is not None:
            with contextlib.suppress(OSError):
                sock.setsockopt(socket.IPPROTO_TCP, option, value)


def _tune_stream_writer(writer: asyncio.StreamWriter) -> None:
    sock = writer.get_extra_info("socket")
    if sock is not None:
        with contextlib.suppress(OSError):
            _tune_tcp_socket(sock)


def _allowed(config: ServiceConfig, peer: Any) -> bool:
    if not config.allowed_clients:
        return True
    if not isinstance(peer, tuple) or not peer:
        return False
    try:
        address = ipaddress.ip_address(peer[0])
    except ValueError:
        return False
    return any(address in network for network in config.allowed_clients)


def _bind_to_interface(sock: socket.socket, interface: str | None) -> None:
    if not interface:
        return
    if not sys.platform.startswith("linux"):
        raise OSError("upstream.interface is supported only on native Linux")
    option = getattr(socket, "SO_BINDTODEVICE", 25)
    sock.setsockopt(socket.SOL_SOCKET, option, interface.encode("utf-8") + b"\0")


async def _resolved_addresses(endpoint: Endpoint, socktype: int) -> list[tuple[Any, ...]]:
    loop = asyncio.get_running_loop()
    return await loop.getaddrinfo(endpoint.host, endpoint.port, type=socktype)


async def open_tcp_upstream(upstream: Upstream, timeout: float) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    loop = asyncio.get_running_loop()
    errors: list[str] = []
    for family, socktype, proto, _, address in await _resolved_addresses(upstream.endpoint, socket.SOCK_STREAM):
        sock = socket.socket(family, socktype, proto)
        sock.setblocking(False)
        try:
            _tune_tcp_socket(sock)
            _bind_to_interface(sock, upstream.interface)
            if upstream.source_ip:
                bind_address: tuple[Any, ...]
                bind_address = (upstream.source_ip, 0, 0, 0) if family == socket.AF_INET6 else (upstream.source_ip, 0)
                sock.bind(bind_address)
            await asyncio.wait_for(loop.sock_connect(sock, address), timeout=timeout)
            return await asyncio.open_connection(sock=sock)
        except Exception as exc:
            errors.append(str(exc))
            sock.close()
    raise ConnectionError(f"cannot connect to {upstream.endpoint}: {'; '.join(errors)}")


async def open_udp_socket(upstream: Upstream, timeout: float) -> socket.socket:
    loop = asyncio.get_running_loop()
    errors: list[str] = []
    for family, socktype, proto, _, address in await _resolved_addresses(upstream.endpoint, socket.SOCK_DGRAM):
        sock = socket.socket(family, socktype, proto)
        sock.setblocking(False)
        try:
            _bind_to_interface(sock, upstream.interface)
            if upstream.source_ip:
                bind_address: tuple[Any, ...]
                bind_address = (upstream.source_ip, 0, 0, 0) if family == socket.AF_INET6 else (upstream.source_ip, 0)
                sock.bind(bind_address)
            await asyncio.wait_for(loop.sock_connect(sock, address), timeout=timeout)
            return sock
        except Exception as exc:
            errors.append(str(exc))
            sock.close()
    raise ConnectionError(f"cannot connect UDP socket to {upstream.endpoint}: {'; '.join(errors)}")


async def _close_writer(writer: asyncio.StreamWriter | None) -> None:
    if writer is None:
        return
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()


class TCPProxy:
    def __init__(self, config: ServiceConfig, stats: ServiceStats) -> None:
        self.config = config
        self.stats = stats
        self.servers: list[asyncio.AbstractServer] = []
        self.tasks: set[asyncio.Task[Any]] = set()

    async def start(self) -> None:
        for endpoint in self.config.listen:
            server = await asyncio.start_server(self._handle_client, endpoint.host, endpoint.port)
            self.servers.append(server)
        self.stats.set_state("running")
        LOGGER.info("%s listening on %s", self.config.name, ", ".join(map(str, self.config.listen)))

    async def stop(self) -> None:
        for server in self.servers:
            server.close()
        await asyncio.gather(*(server.wait_closed() for server in self.servers), return_exceptions=True)
        self.servers.clear()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        self.stats.set_state("stopped")

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        _tune_stream_writer(writer)
        task = asyncio.current_task()
        if task:
            self.tasks.add(task)
        peer = writer.get_extra_info("peername")
        if not _allowed(self.config, peer):
            self.stats.dropped_clients += 1
            LOGGER.warning("%s rejected client %s", self.config.name, peer)
            await _close_writer(writer)
            if task:
                self.tasks.discard(task)
            return
        upstream_writer: asyncio.StreamWriter | None = None
        self.stats.total_connections += 1
        try:
            upstream_reader, upstream_writer = await open_tcp_upstream(
                self.config.upstream, self.config.connect_timeout_seconds
            )
            self.stats.active_clients += 1
            await self._bridge(reader, writer, upstream_reader, upstream_writer)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.stats.set_state("running", exc)
            LOGGER.warning("%s connection %s failed: %s", self.config.name, peer, exc)
        finally:
            if upstream_writer is not None:
                self.stats.active_clients = max(0, self.stats.active_clients - 1)
            await asyncio.gather(_close_writer(writer), _close_writer(upstream_writer))
            if task:
                self.tasks.discard(task)

    async def _bridge(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        upstream_reader: asyncio.StreamReader,
        upstream_writer: asyncio.StreamWriter,
    ) -> None:
        async def pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, from_client: bool) -> None:
            while True:
                read = reader.read(BUFFER_SIZE)
                data = (
                    await asyncio.wait_for(read, timeout=self.config.idle_timeout_seconds)
                    if self.config.idle_timeout_seconds > 0
                    else await read
                )
                if not data:
                    return
                writer.write(data)
                await writer.drain()
                if from_client:
                    self.stats.bytes_from_clients += len(data)
                    self.stats.bytes_to_upstream += len(data)
                else:
                    self.stats.bytes_from_upstream += len(data)
                    self.stats.bytes_to_clients += len(data)

        tasks = {
            asyncio.create_task(pump(client_reader, upstream_writer, True)),
            asyncio.create_task(pump(upstream_reader, client_writer, False)),
        }
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*done, *pending, return_exceptions=True)


@dataclass
class BroadcastClient:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    queue: asyncio.Queue[bytes | None]
    sender: asyncio.Task[Any]


class BroadcastProxy:
    def __init__(self, config: ServiceConfig, stats: ServiceStats) -> None:
        self.config = config
        self.stats = stats
        self.servers: list[asyncio.AbstractServer] = []
        self.clients: dict[int, BroadcastClient] = {}
        self.client_tasks: set[asyncio.Task[Any]] = set()
        self.upstream_task: asyncio.Task[Any] | None = None
        self.upstream_writer: asyncio.StreamWriter | None = None
        self.upstream_write_lock = asyncio.Lock()
        self.stopping = False

    async def start(self) -> None:
        self.stopping = False
        for endpoint in self.config.listen:
            server = await asyncio.start_server(self._handle_client, endpoint.host, endpoint.port)
            self.servers.append(server)
        self.stats.set_state("running")
        self.upstream_task = asyncio.create_task(self._upstream_supervisor(), name=f"{self.config.name}-upstream")
        LOGGER.info("%s broadcast listeners on %s", self.config.name, ", ".join(map(str, self.config.listen)))

    async def stop(self) -> None:
        self.stopping = True
        for server in self.servers:
            server.close()
        await asyncio.gather(*(server.wait_closed() for server in self.servers), return_exceptions=True)
        self.servers.clear()
        if self.upstream_task:
            self.upstream_task.cancel()
        for client in list(self.clients.values()):
            client.writer.close()
            client.sender.cancel()
        for task in list(self.client_tasks):
            task.cancel()
        await asyncio.gather(
            *([self.upstream_task] if self.upstream_task else []),
            *self.client_tasks,
            return_exceptions=True,
        )
        await _close_writer(self.upstream_writer)
        self.clients.clear()
        self.client_tasks.clear()
        self.upstream_task = None
        self.upstream_writer = None
        self.stats.set_state("stopped")

    async def _upstream_supervisor(self) -> None:
        first_attempt = True
        while not self.stopping:
            reader: asyncio.StreamReader | None = None
            writer: asyncio.StreamWriter | None = None
            try:
                reader, writer = await open_tcp_upstream(self.config.upstream, self.config.connect_timeout_seconds)
                self.upstream_writer = writer
                self.stats.upstream_connected = True
                self.stats.set_state("connected")
                if not first_attempt:
                    self.stats.reconnects += 1
                first_attempt = False
                LOGGER.info("%s connected to RPM upstream", self.config.name)
                while True:
                    data = await reader.read(BUFFER_SIZE)
                    if not data:
                        raise ConnectionResetError("RPM upstream closed the connection")
                    self.stats.bytes_from_upstream += len(data)
                    for key, client in list(self.clients.items()):
                        try:
                            client.queue.put_nowait(data)
                        except asyncio.QueueFull:
                            self.stats.dropped_clients += 1
                            LOGGER.warning("%s dropping slow client", self.config.name)
                            client.writer.close()
                            self.clients.pop(key, None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.stats.upstream_connected = False
                self.stats.set_state("reconnecting", exc)
                LOGGER.warning("%s RPM upstream unavailable: %s", self.config.name, exc)
            finally:
                self.upstream_writer = None
                await _close_writer(writer)
                if self.config.disconnect_clients_on_upstream_loss:
                    for client in list(self.clients.values()):
                        client.writer.close()
                self.stats.upstream_connected = False
            if not self.stopping:
                await asyncio.sleep(self.config.reconnect_delay_seconds)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        _tune_stream_writer(writer)
        task = asyncio.current_task()
        if task:
            self.client_tasks.add(task)
        peer = writer.get_extra_info("peername")
        if not _allowed(self.config, peer):
            self.stats.dropped_clients += 1
            await _close_writer(writer)
            if task:
                self.client_tasks.discard(task)
            return
        key = id(writer)
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=self.config.queue_packets)
        sender = asyncio.create_task(self._client_sender(writer, queue), name=f"{self.config.name}-sender")
        self.clients[key] = BroadcastClient(reader=reader, writer=writer, queue=queue, sender=sender)
        self.stats.total_connections += 1
        self.stats.active_clients += 1
        LOGGER.info("%s accepted CAS client %s", self.config.name, peer)
        try:
            while True:
                data = await reader.read(BUFFER_SIZE)
                if not data:
                    break
                self.stats.bytes_from_clients += len(data)
                if self.config.client_writes == "forward":
                    async with self.upstream_write_lock:
                        if self.upstream_writer is None:
                            raise ConnectionError("RPM upstream is not connected")
                        self.upstream_writer.write(data)
                        await self.upstream_writer.drain()
                        self.stats.bytes_to_upstream += len(data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.info("%s CAS client %s closed: %s", self.config.name, peer, exc)
        finally:
            self.clients.pop(key, None)
            self.stats.active_clients = max(0, self.stats.active_clients - 1)
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
            await _close_writer(writer)
            if task:
                self.client_tasks.discard(task)

    async def _client_sender(self, writer: asyncio.StreamWriter, queue: asyncio.Queue[bytes | None]) -> None:
        while True:
            data = await queue.get()
            if data is None:
                return
            writer.write(data)
            await writer.drain()
            self.stats.bytes_to_clients += len(data)


class UDPFrontProtocol(asyncio.DatagramProtocol):
    def __init__(self, service: "UDPProxy") -> None:
        self.service = service
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[Any, ...]) -> None:
        if self.transport is not None:
            self.service.receive_client(data, addr, self.transport)

    def error_received(self, exc: Exception) -> None:
        self.service.stats.set_state("running", exc)


class UDPUpstreamProtocol(asyncio.DatagramProtocol):
    def __init__(self, service: "UDPProxy", key: tuple[int, tuple[Any, ...]]) -> None:
        self.service = service
        self.key = key

    def datagram_received(self, data: bytes, addr: tuple[Any, ...]) -> None:
        self.service.receive_upstream(self.key, data)

    def error_received(self, exc: Exception) -> None:
        self.service.stats.set_state("running", exc)


@dataclass
class UDPSession:
    transport: asyncio.DatagramTransport
    front_transport: asyncio.DatagramTransport
    client: tuple[Any, ...]
    last_activity: float


class UDPProxy:
    def __init__(self, config: ServiceConfig, stats: ServiceStats) -> None:
        self.config = config
        self.stats = stats
        self.transports: list[asyncio.DatagramTransport] = []
        self.sessions: dict[tuple[int, tuple[Any, ...]], UDPSession] = {}
        self.pending: dict[tuple[int, tuple[Any, ...]], list[bytes]] = {}
        self.tasks: set[asyncio.Task[Any]] = set()
        self.cleaner: asyncio.Task[Any] | None = None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        for endpoint in self.config.listen:
            transport, _ = await loop.create_datagram_endpoint(
                lambda: UDPFrontProtocol(self), local_addr=(endpoint.host, endpoint.port)
            )
            self.transports.append(transport)  # type: ignore[arg-type]
        self.cleaner = asyncio.create_task(self._clean_sessions(), name=f"{self.config.name}-udp-cleaner")
        self.stats.set_state("running")
        LOGGER.info("%s UDP listeners on %s", self.config.name, ", ".join(map(str, self.config.listen)))

    async def stop(self) -> None:
        if self.cleaner:
            self.cleaner.cancel()
        for task in list(self.tasks):
            task.cancel()
        for session in self.sessions.values():
            session.transport.close()
        for transport in self.transports:
            transport.close()
        await asyncio.gather(*self.tasks, *([self.cleaner] if self.cleaner else []), return_exceptions=True)
        self.tasks.clear()
        self.transports.clear()
        self.sessions.clear()
        self.pending.clear()
        self.cleaner = None
        self.stats.set_state("stopped")

    def receive_client(
        self,
        data: bytes,
        client: tuple[Any, ...],
        front_transport: asyncio.DatagramTransport,
    ) -> None:
        if not _allowed(self.config, client):
            self.stats.dropped_clients += 1
            return
        self.stats.bytes_from_clients += len(data)
        key = (id(front_transport), client)
        session = self.sessions.get(key)
        if session:
            session.last_activity = time.monotonic()
            session.transport.sendto(data)
            self.stats.bytes_to_upstream += len(data)
            return
        queue = self.pending.setdefault(key, [])
        if len(queue) >= self.config.queue_packets:
            self.stats.dropped_clients += 1
            return
        queue.append(data)
        if len(queue) == 1:
            task = asyncio.create_task(
                self._create_session(key, client, front_transport),
                name=f"{self.config.name}-udp-session",
            )
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def _create_session(
        self,
        key: tuple[int, tuple[Any, ...]],
        client: tuple[Any, ...],
        front_transport: asyncio.DatagramTransport,
    ) -> None:
        try:
            sock = await open_udp_socket(self.config.upstream, self.config.connect_timeout_seconds)
            loop = asyncio.get_running_loop()
            transport, _ = await loop.create_datagram_endpoint(
                lambda: UDPUpstreamProtocol(self, key), sock=sock
            )
            session = UDPSession(  # type: ignore[arg-type]
                transport=transport,
                front_transport=front_transport,
                client=client,
                last_activity=time.monotonic(),
            )
            self.sessions[key] = session
            self.stats.total_connections += 1
            self.stats.active_clients = len(self.sessions)
            for data in self.pending.pop(key, []):
                session.transport.sendto(data)
                self.stats.bytes_to_upstream += len(data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.pending.pop(key, None)
            self.stats.set_state("running", exc)
            LOGGER.warning("%s UDP session for %s failed: %s", self.config.name, client, exc)

    def receive_upstream(self, key: tuple[int, tuple[Any, ...]], data: bytes) -> None:
        session = self.sessions.get(key)
        if not session:
            return
        session.last_activity = time.monotonic()
        session.front_transport.sendto(data, session.client)
        self.stats.bytes_from_upstream += len(data)
        self.stats.bytes_to_clients += len(data)

    async def _clean_sessions(self) -> None:
        while True:
            await asyncio.sleep(min(5.0, self.config.udp_session_timeout_seconds))
            cutoff = time.monotonic() - self.config.udp_session_timeout_seconds
            for key, session in list(self.sessions.items()):
                if session.last_activity < cutoff:
                    session.transport.close()
                    self.sessions.pop(key, None)
            self.stats.active_clients = len(self.sessions)


def create_service(config: ServiceConfig, stats: ServiceStats) -> TCPProxy | BroadcastProxy | UDPProxy:
    if config.kind == "tcp":
        return TCPProxy(config, stats)
    if config.kind == "rpm_broadcast":
        return BroadcastProxy(config, stats)
    if config.kind == "udp":
        return UDPProxy(config, stats)
    raise ValueError(f"unsupported service kind {config.kind}")
