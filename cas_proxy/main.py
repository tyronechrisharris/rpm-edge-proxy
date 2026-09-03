from __future__ import annotations

import argparse
import asyncio
import errno
import logging
import os
import signal
import sys
import urllib.request
from pathlib import Path

from . import __version__
from .config import ConfigError, load_config
from .http_status import StatusServer
from .network import create_service
from .state import Registry
from .watchdog import EventLoopWatchdog


LOGGER = logging.getLogger("cas_proxy")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reliable radiation portal monitor TCP data proxy")
    parser.add_argument("--config", default="/config/config.json", help="path to JSON configuration")
    parser.add_argument("--validate", action="store_true", help="validate configuration and exit")
    parser.add_argument("--check", metavar="URL", help="HTTP health check URL and exit")
    parser.add_argument("--version", action="version", version=__version__)
    return parser.parse_args()


def _health_check(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return 0 if response.status == 200 else 1
    except Exception:
        return 1


async def run(config_path: Path) -> None:
    config = load_config(config_path)
    logging.getLogger().setLevel(config.log_level)
    registry = Registry()
    services = []
    for service_config in config.services:
        if not service_config.enabled:
            continue
        stats = registry.register(service_config)
        services.append(create_service(service_config, stats))
    status = StatusServer(config.status_listen, registry)
    started = []
    watchdog_timeout = float(os.environ.get("CAS_PROXY_WATCHDOG_SECONDS", "30"))
    watchdog = EventLoopWatchdog(watchdog_timeout)
    watchdog.start()
    watchdog_task = asyncio.create_task(watchdog.pulse(), name="event-loop-watchdog-pulse")
    try:
        while True:
            started.clear()
            try:
                for service in services:
                    try:
                        await service.start()
                    except Exception:
                        await service.stop()
                        raise
                    started.append(service)
                await status.start()
                break
            except OSError as exc:
                for service in reversed(started):
                    await service.stop()
                if exc.errno != errno.EADDRNOTAVAIL:
                    raise
                LOGGER.warning(
                    "listener address is not assigned yet; waiting for NetworkManager: %s",
                    exc,
                )
                await asyncio.sleep(2)
    except Exception:
        watchdog_task.cancel()
        await asyncio.gather(watchdog_task, return_exceptions=True)
        watchdog.stop()
        for service in reversed(started):
            await service.stop()
        raise
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass
    LOGGER.info("CAS Edge Proxy %s started with %d service(s)", __version__, len(services))
    await stop_event.wait()
    LOGGER.info("shutdown requested")
    await status.stop()
    for service in reversed(services):
        await service.stop()
    watchdog_task.cancel()
    await asyncio.gather(watchdog_task, return_exceptions=True)
    watchdog.stop()


def main() -> None:
    args = _arguments()
    if args.check:
        raise SystemExit(_health_check(args.check))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        config = load_config(args.config)
        if args.validate:
            enabled = sum(1 for service in config.services if service.enabled)
            print(f"configuration valid: {enabled} enabled service(s)")
            return
        asyncio.run(run(Path(args.config)))
    except ConfigError as exc:
        LOGGER.error("configuration error: %s", exc)
        raise SystemExit(2) from exc
    except KeyboardInterrupt:
        return
    except Exception as exc:
        LOGGER.error("fatal error: %s", exc, exc_info=True)
        raise SystemExit(1) from exc
