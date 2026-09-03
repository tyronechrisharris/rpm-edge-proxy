import asyncio
import unittest

from cas_proxy.config import Endpoint, ServiceConfig, Upstream
from cas_proxy.http_status import StatusServer
from cas_proxy.state import Registry


class StatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_and_degraded_readiness(self):
        registry = Registry()
        config = ServiceConfig(
            name="required-rpm",
            kind="rpm_broadcast",
            listen=(Endpoint("127.0.0.1", 11601),),
            upstream=Upstream(Endpoint("127.0.0.1", 1600)),
            required=True,
        )
        registry.register(config)
        server = StatusServer(Endpoint("127.0.0.1", 0), registry)
        await server.start()
        try:
            port = server.server.sockets[0].getsockname()[1]
            health = await self._get(port, "/healthz")
            ready = await self._get(port, "/readyz")
            self.assertIn(b"200 OK", health)
            self.assertIn(b"503 Service Unavailable", ready)
        finally:
            await server.stop()

    async def _get(self, port, path):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
        await writer.drain()
        data = await reader.read()
        writer.close()
        await writer.wait_closed()
        return data


if __name__ == "__main__":
    unittest.main()
