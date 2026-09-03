import unittest

from cas_proxy.watchdog import EventLoopWatchdog


class WatchdogTests(unittest.TestCase):
    def test_rejects_unsafe_short_timeout(self):
        with self.assertRaises(ValueError):
            EventLoopWatchdog(4)

    def test_accepts_configured_timeout(self):
        watchdog = EventLoopWatchdog(30, exit_function=lambda _code: None)
        self.assertEqual(watchdog.timeout_seconds, 30)


if __name__ == "__main__":
    unittest.main()
