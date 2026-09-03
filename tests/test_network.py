import asyncio
import socket
import unittest

from cas_proxy.config import Endpoint, ServiceConfig, Upstream
from cas_proxy.network import BroadcastProxy, TCPProxy, UDPProxy
from cas_proxy.state import ServiceStats


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class NetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_bidirectional_tcp_proxy(self):
        async def echo(reader, writer):
            try:
                while data := await reader.read(65536):
                    writer.write(data)
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        upstream = await asyncio.start_server(echo, "127.0.0.1", 0)
        upstream_port = upstream.sockets[0].getsockname()[1]
        listen_port = free_port()
        config = ServiceConfig(
            name="tcp-test",
            kind="tcp",
            listen=(Endpoint("127.0.0.1", listen_port),),
            upstream=Upstream(Endpoint("127.0.0.1", upstream_port)),
        )
        stats = ServiceStats.from_config(config)
        proxy = TCPProxy(config, stats)
        await proxy.start()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", listen_port)
            writer.write(b"hello-rpm")
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.readexactly(9), 2), b"hello-rpm")
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0.05)
            self.assertEqual(stats.bytes_from_clients, 9)
            self.assertEqual(stats.bytes_to_clients, 9)
        finally:
            await proxy.stop()
            upstream.close()
            await upstream.wait_closed()

    async def test_rpm_broadcast_to_two_clients_and_discards_writes(self):
        upstream_writers = []
        upstream_connected = asyncio.Event()

        async def rpm(reader, writer):
            upstream_writers.append(writer)
            upstream_connected.set()
            try:
                await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()

        upstream = await asyncio.start_server(rpm, "127.0.0.1", 0)
        upstream_port = upstream.sockets[0].getsockname()[1]
        listen_port = free_port()
        config = ServiceConfig(
            name="broadcast-test",
            kind="rpm_broadcast",
            listen=(Endpoint("127.0.0.1", listen_port),),
            upstream=Upstream(Endpoint("127.0.0.1", upstream_port)),
            reconnect_delay_seconds=0.05,
            client_writes="discard",
        )
        stats = ServiceStats.from_config(config)
        proxy = BroadcastProxy(config, stats)
        await proxy.start()
        try:
            await asyncio.wait_for(upstream_connected.wait(), 2)
            reader1, writer1 = await asyncio.open_connection("127.0.0.1", listen_port)
            reader2, writer2 = await asyncio.open_connection("127.0.0.1", listen_port)
            await asyncio.sleep(0.05)
            upstream_writers[0].write(b"scan-data")
            await upstream_writers[0].drain()
            self.assertEqual(await asyncio.wait_for(reader1.readexactly(9), 2), b"scan-data")
            self.assertEqual(await asyncio.wait_for(reader2.readexactly(9), 2), b"scan-data")
            writer1.write(b"ignored")
            await writer1.drain()
            await asyncio.sleep(0.05)
            self.assertEqual(stats.bytes_from_clients, 7)
            self.assertEqual(stats.bytes_to_upstream, 0)
            writer1.close()
            writer2.close()
            await asyncio.gather(writer1.wait_closed(), writer2.wait_closed())
            upstream_connected.clear()
            upstream_writers[0].close()
            await upstream_writers[0].wait_closed()
            await asyncio.wait_for(upstream_connected.wait(), 2)
            self.assertGreaterEqual(stats.reconnects, 1)
            await proxy.stop()
            upstream_connected.clear()
            await proxy.start()
            await asyncio.wait_for(upstream_connected.wait(), 2)
            self.assertTrue(stats.upstream_connected)
        finally:
            await proxy.stop()
            upstream.close()
            await upstream.wait_closed()

    async def test_udp_proxy_round_trip(self):
        class EchoProtocol(asyncio.DatagramProtocol):
            def connection_made(self, transport):
                self.transport = transport

            def datagram_received(self, data, addr):
                self.transport.sendto(data, addr)

        class ClientProtocol(asyncio.DatagramProtocol):
            def __init__(self):
                self.received = asyncio.get_running_loop().create_future()

            def datagram_received(self, data, addr):
                if not self.received.done():
                    self.received.set_result(data)

        loop = asyncio.get_running_loop()
        upstream_transport, _ = await loop.create_datagram_endpoint(
            EchoProtocol, local_addr=("127.0.0.1", 0)
        )
        upstream_port = upstream_transport.get_extra_info("sockname")[1]
        listen_port = free_port()
        config = ServiceConfig(
            name="udp-test",
            kind="udp",
            listen=(Endpoint("127.0.0.1", listen_port),),
            upstream=Upstream(Endpoint("127.0.0.1", upstream_port)),
            udp_session_timeout_seconds=0.2,
        )
        stats = ServiceStats.from_config(config)
        proxy = UDPProxy(config, stats)
        await proxy.start()
        client_transport, client_protocol = await loop.create_datagram_endpoint(
            ClientProtocol, local_addr=("127.0.0.1", 0)
        )
        try:
            client_transport.sendto(b"udp-device", ("127.0.0.1", listen_port))
            self.assertEqual(
                await asyncio.wait_for(client_protocol.received, 2),
                b"udp-device",
            )
            self.assertEqual(stats.bytes_from_clients, 10)
            self.assertEqual(stats.bytes_to_clients, 10)
        finally:
            client_transport.close()
            await proxy.stop()
            upstream_transport.close()


if __name__ == "__main__":
    unittest.main()
