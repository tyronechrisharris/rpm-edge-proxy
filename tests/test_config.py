import json
import tempfile
import unittest
from pathlib import Path

from cas_proxy.config import ConfigError, Endpoint, load_config


class ConfigTests(unittest.TestCase):
    def _load(self, payload):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return load_config(path)

    def test_endpoint_ipv4_and_ipv6(self):
        self.assertEqual(Endpoint.parse("127.0.0.1:1600", "test").port, 1600)
        endpoint = Endpoint.parse("[::1]:9090", "test")
        self.assertEqual(endpoint.host, "::1")
        self.assertEqual(str(endpoint), "[::1]:9090")

    def test_minimal_tcp_configuration(self):
        config = self._load(
            {
                "version": 1,
                "services": [
                    {
                        "name": "rpm-1",
                        "kind": "tcp",
                        "listen": ["127.0.0.1:11601"],
                        "upstream": {"address": "127.0.0.1:1600"},
                    }
                ],
            }
        )
        self.assertEqual(config.services[0].name, "rpm-1")
        self.assertEqual(str(config.status_listen), "0.0.0.0:9090")

    def test_duplicate_listener_rejected(self):
        payload = {
            "version": 1,
            "services": [
                {
                    "name": "one",
                    "kind": "tcp",
                    "listen": ["127.0.0.1:1600"],
                    "upstream": {"address": "127.0.0.1:2600"},
                },
                {
                    "name": "two",
                    "kind": "tcp",
                    "listen": ["127.0.0.1:1600"],
                    "upstream": {"address": "127.0.0.1:3600"},
                },
            ],
        }
        with self.assertRaises(ConfigError):
            self._load(payload)

    def test_unknown_field_rejected(self):
        payload = {
            "version": 1,
            "services": [
                {
                    "name": "one",
                    "kind": "tcp",
                    "listen": ["127.0.0.1:1600"],
                    "upstream": {"address": "127.0.0.1:2600"},
                    "typo": True,
                }
            ],
        }
        with self.assertRaises(ConfigError):
            self._load(payload)

    def test_production_mapping(self):
        project_dir = Path(__file__).resolve().parents[1]
        config = load_config(project_dir / "config" / "config.json")
        self.assertEqual(len(config.services), 1)
        rpm = config.services[0]
        self.assertEqual(str(rpm.listen[0]), "192.168.2.2:1600")
        self.assertEqual(str(rpm.upstream.endpoint), "192.168.2.3:1600")
        self.assertEqual(rpm.upstream.source_ip, "192.168.5.10")
        self.assertEqual(rpm.client_writes, "discard")
        self.assertTrue(rpm.required)


if __name__ == "__main__":
    unittest.main()
