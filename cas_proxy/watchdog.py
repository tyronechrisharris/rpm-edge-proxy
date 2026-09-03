from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections.abc import Callable


LOGGER = logging.getLogger("cas_proxy.watchdog")


class EventLoopWatchdog:
    """Exit the process if the asyncio event loop stops making progress."""

    def __init__(
        self,
        timeout_seconds: float,
        *,
        exit_function: Callable[[int], None] = os._exit,
    ) -> None:
        if timeout_seconds < 5:
            raise ValueError("watchdog timeout must be at least 5 seconds")
        self.timeout_seconds = timeout_seconds
        self.exit_function = exit_function
        self._last_pulse = time.monotonic()
        self._stopped = threading.Event()
        self._thread = threading.Thread(
            target=self._monitor,
            name="event-loop-watchdog",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()
        LOGGER.info("event-loop watchdog armed at %.1f seconds", self.timeout_seconds)

    def stop(self) -> None:
        self._stopped.set()
        self._thread.join(timeout=2)

    async def pulse(self) -> None:
        while not self._stopped.is_set():
            self._last_pulse = time.monotonic()
            await asyncio.sleep(1)

    def _monitor(self) -> None:
        check_interval = min(5.0, self.timeout_seconds / 3)
        while not self._stopped.wait(check_interval):
            stale_for = time.monotonic() - self._last_pulse
            if stale_for <= self.timeout_seconds:
                continue
            LOGGER.critical(
                "event loop has not advanced for %.1f seconds; exiting for Docker restart",
                stale_for,
            )
            self.exit_function(70)
            return
