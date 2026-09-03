from __future__ import annotations

import asyncio
import json
import logging
from http import HTTPStatus

from .config import Endpoint
from .state import Registry


LOGGER = logging.getLogger("cas_proxy.status")


class StatusServer:
    def __init__(self, endpoint: Endpoint, registry: Registry) -> None:
        self.endpoint = endpoint
        self.registry = registry
        self.server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, self.endpoint.host, self.endpoint.port)
        LOGGER.info("status server listening on %s", self.endpoint)

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
            if len(raw) > 16384:
                await self._respond(writer, HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE, b"headers too large\n", "text/plain")
                return
            request_line = raw.split(b"\r\n", 1)[0].decode("ascii", "replace")
            parts = request_line.split()
            if len(parts) != 3 or parts[0] != "GET":
                await self._respond(writer, HTTPStatus.METHOD_NOT_ALLOWED, b"GET required\n", "text/plain")
                return
            path = parts[1].split("?", 1)[0]
            if path in {"/", "/healthz"}:
                await self._respond(writer, HTTPStatus.OK, b"ok\n", "text/plain")
            elif path == "/readyz":
                ready = self.registry.ready()
                await self._respond(
                    writer,
                    HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE,
                    b"ready\n" if ready else b"degraded\n",
                    "text/plain",
                )
            elif path == "/status":
                body = (json.dumps(self.registry.snapshot(), indent=2, sort_keys=True) + "\n").encode()
                await self._respond(writer, HTTPStatus.OK, body, "application/json")
            elif path == "/metrics":
                await self._respond(writer, HTTPStatus.OK, self.registry.metrics().encode(), "text/plain; version=0.0.4")
            else:
                await self._respond(writer, HTTPStatus.NOT_FOUND, b"not found\n", "text/plain")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            pass
        except Exception as exc:
            LOGGER.debug("status request failed: %s", exc)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _respond(
        self, writer: asyncio.StreamWriter, status: HTTPStatus, body: bytes, content_type: str
    ) -> None:
        headers = (
            f"HTTP/1.1 {status.value} {status.phrase}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Cache-Control: no-store\r\n"
            "Connection: close\r\n"
            "X-Content-Type-Options: nosniff\r\n"
            "\r\n"
        ).encode("ascii")
        writer.write(headers + body)
        await writer.drain()
