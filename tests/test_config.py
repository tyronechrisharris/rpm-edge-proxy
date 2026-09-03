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
        self.assertEqual(len(config.services), 2)
        export, import_lane = config.services
        self.assertEqual(str(export.listen[0]), "172.26.0.33:1600")
        self.assertEqual(str(export.upstream.endpoint), "172.26.0.32:1600")
        self.assertEqual(export.upstream.source_ip, "172.26.0.51")
        self.assertEqual(export.client_writes, "discard")
        self.assertTrue(export.required)
        self.assertEqual(str(import_lane.listen[0]), "172.26.0.65:1600")
        self.assertEqual(str(import_lane.upstream.endpoint), "172.26.0.64:1600")
        self.assertEqual(import_lane.upstream.source_ip, "172.26.0.51")
        self.assertEqual(import_lane.client_writes, "discard")
        self.assertTrue(import_lane.required)


if __name__ == "__main__":
    unittest.main()
