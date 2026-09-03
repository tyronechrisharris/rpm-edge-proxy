# RPM Edge Proxy

This package provides a dedicated Raspberry Pi gateway for two radiation portal monitor (RPM) TCP data feeds. It does not proxy cameras.

## Final address plan

The Raspberry Pi uses one physical Ethernet interface with three IPv4 addresses. `172.26.0.51` is the Pi management and upstream source address. `172.26.0.33` and `172.26.0.65` are service addresses that replace the RPMs' old addresses.

| Function | Client-facing listener | Real upstream RPM | Direction |
| --- | --- | --- | --- |
| Export Gate Lane 1 | `172.26.0.33:1600` | `172.26.0.32:1600` | RPM to CAS data |
| Import Gate Lane 2 | `172.26.0.65:1600` | `172.26.0.64:1600` | RPM to CAS data |
| Pi management/source | `172.26.0.51` | n/a | SSH and outbound source |

The existing central alarm system (CAS) continues using `172.26.0.33:1600` and `172.26.0.65:1600`; no CAS endpoint change is required.

This package assumes subnet `172.26.0.0/24`, a wired interface named `eth0`, and no gateway or DNS requirement on this isolated RPM network. Change the prefix or interface in the scripts before deployment if the site differs.

## Proxy behavior

Each configured service:

- opens one persistent TCP connection to its real RPM;
- immediately forwards received bytes to every connected CAS client without parsing or altering them;
- discards CAS-to-RPM bytes because this deployment is explicitly data-only;
- drops slow CAS clients instead of delaying current RPM data;
- disconnects CAS clients when an upstream fails, so they establish a clean session after recovery;
- reconnects to an unavailable or rebooted RPM every two seconds.

This is a low-latency, soft-real-time TCP relay. It is not a hard-real-time or safety-certified control system. It does not buffer historical data while an RPM or CAS is disconnected.

## Reliability design

- Raspberry Pi OS Lite 64-bit provides the smallest officially supported Pi operating system footprint.
- NetworkManager persistently owns `.51`, `.33`, and `.65` and restores them at every boot.
- A NetworkManager dispatcher announces all three addresses after link activation so neighboring ARP caches learn the Pi's MAC promptly.
- Docker host networking lets the unprivileged process bind the original IP addresses and port `1600` directly.
- `restart: always` restarts the service after a crash and whenever Docker returns after power loss.
- The proxy maintains each RPM connection internally and detects half-open TCP sessions with keepalive settings.
- An independent in-process watchdog exits if the event loop stalls; Docker then restarts it.
- The optional systemd hardware watchdog reboots the Pi if the operating system becomes unresponsive.
- The container filesystem and configuration are read-only, Linux capabilities are dropped, process and memory limits are applied, and log growth is bounded.
- `/healthz`, `/readyz`, `/status`, and `/metrics` are available only on `127.0.0.1:9090`.

## Recommended field hardware

For an industrial installation, use a Raspberry Pi Compute Module with eMMC or a Raspberry Pi 5 booting from a high-endurance SSD. Avoid a consumer microSD card as the long-term system disk. Use wired Ethernet, a ventilated or rated enclosure, and a regulated industrial power supply with UPS or DC hold-up. A single Pi remains a single point of failure; true high availability requires a second independently powered gateway and a controlled floating-IP failover design.

## Deployment sequence

Do these steps from a local keyboard and display. Applying the static profile can disconnect SSH.

1. Back up the two RPM configurations.
2. Change the Export Gate Lane 1 RPM from `172.26.0.33` to `172.26.0.32`.
3. Change the Import Gate Lane 2 RPM from `172.26.0.65` to `172.26.0.64`.
4. Confirm that no device still owns `.33` or `.65`.
5. Flash the current Raspberry Pi OS Lite 64-bit release and boot it.
6. Install Docker Engine with Compose v2, plus `iproute2` and `iputils-arping`.
7. Copy this directory to the Pi and run:

   ```bash
   sudo ./scripts/install-pi.sh --apply-network --interface eth0
   ```

The installer performs duplicate-address detection before activating the network, copies the deployment to `/opt/rpm-edge-proxy`, enables Docker at boot, installs the hardware-watchdog configuration when `/dev/watchdog` is available, builds or uses the pinned image, starts it, and verifies the listeners.

Reboot once after installation, then verify:

```bash
sudo /opt/rpm-edge-proxy/scripts/verify.sh eth0
```

## Offline image workflow

On an internet-connected computer with Docker Buildx, build the ARM64 archive:

```bash
./scripts/build-offline-bundles.sh 1.1.0
```

Copy the complete directory, including `dist/rpm-edge-proxy-1.1.0-linux-arm64.tar.gz`, to the Pi, then run the installer:

```bash
sudo ./scripts/install-pi.sh --apply-network --interface eth0
```

The installer loads the local archive automatically and does not contact an image registry. You can also load the archive manually with `sudo docker load --input <archive>`.

## Operations

```bash
# Overall state
sudo /opt/rpm-edge-proxy/scripts/verify.sh eth0

# Live proxy state and byte counters
curl http://127.0.0.1:9090/status

# Readiness: HTTP 200 only when both upstream RPMs are connected
curl --fail http://127.0.0.1:9090/readyz

# Recent bounded logs
docker logs --tail 200 rpm-edge-proxy

# Deliberate restart
docker restart rpm-edge-proxy
```

See `RUNBOOK.md` for commissioning and failure testing. See `SECURITY.md` for the production checklist.

## Configuration

The production configuration is `config/config.json`. The filename inside the container remains `/config/config.json`; do not rename it. Unknown JSON fields, malformed addresses, and duplicate listeners stop startup rather than being silently ignored.

The current settings deliberately use `rpm_broadcast` and `client_writes: "discard"`. Do not change to `forward` unless the RPM protocol owner confirms that the CAS must send commands and that multiple controllers are safe.

Validate source, configuration, and shell scripts without Docker:

```bash
./scripts/validate.sh
```

## Important cutover constraint

Never assign `.33` or `.65` to the Pi while either old RPM still uses that address. Duplicate IP ownership can cause intermittent traffic to reach the wrong MAC address and can appear to work briefly before failing as ARP caches change. The included preflight script blocks installation when it detects such a conflict.
