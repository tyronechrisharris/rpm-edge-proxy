# Production Security Checklist

- Keep the Pi and RPM segment isolated; no internet route is required for proxy operation.
- Permit administration only from the authorized management network or physical console.
- Disable password SSH login after installing administrator public keys.
- Keep `allowed_clients` restricted to the actual CAS subnet; the supplied `/24` can be narrowed when the CAS addresses are known.
- Keep `client_writes` set to `discard` for this data-only deployment.
- Do not publish the localhost status service through a reverse proxy.
- Keep the container read-only, unprivileged, capability-free, and in host-network mode only for the two explicit listeners.
- Pin and scan the Python base-image digest before a controlled production release.
- Verify SHA-256 checksums before loading an offline image.
- Use a tested, high-endurance system disk and bounded Docker logs.
- Back up the validated configuration and NetworkManager profile offline.
- Apply operating-system and image updates in a planned maintenance window, followed by all failure tests in `RUNBOOK.md`.
- Periodically verify that the hardware watchdog is active with `systemctl show --property RuntimeWatchdogUSec`.

The proxy does not originate internet connections, terminate encryption, store credentials, parse RPM payloads, or proxy any camera traffic.
