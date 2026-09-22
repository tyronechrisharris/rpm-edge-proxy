# RPM Edge Proxy

RPM Edge Proxy is a small, dependency-free TCP fan-out service for one radiation portal monitor (RPM). It keeps one persistent connection to the real RPM and copies the byte stream to every connected consumer in real time.

```text
PC / OSCAR #1 ─┐
PC / OSCAR #2 ─┼── TCP 192.168.2.2:1600 ── proxy ── one TCP connection ── RPM 192.168.2.3:1600
PC / OSCAR #3 ─┘
```

The proxy does not parse, reframe, store, or modify RPM messages. `TCP_NODELAY` is enabled on both sides. Each consumer has its own bounded queue, so a stalled computer is disconnected instead of delaying the live feed for everyone else.

## Network plan

| Device | Address | Purpose |
| --- | --- | --- |
| Raspberry Pi or Windows 11 host | `192.168.2.2/24` | Proxy listener |
| Actual RPM | `192.168.2.3/24` | Upstream TCP server |
| Consumer computers | `192.168.2.0/24` | Connect to `192.168.2.2:1600` |
| Windows 11 host, optional second address | `192.168.3.99/24` | Direct route to camera `192.168.3.2` |

Only one device may own `192.168.2.2`. Do not run the Pi and Windows deployments at the same time unless one uses a different address and the configuration is deliberately changed.

## Protocol assumptions

This build is intentionally configured for the common data-only arrangement in which:

- the RPM accepts a TCP connection on port `1600`;
- the RPM sends its feed without needing commands from each consumer; and
- consumer-to-RPM writes are not required.

Bytes sent by consumers are read and discarded. This prevents several computers from issuing conflicting commands through one RPM connection. If the RPM requires request/response commands, confirm that behavior before deployment; multi-controller arbitration is a different design.

This is a soft-real-time relay, not a hard-real-time or safety-certified component. It adds no intentional delay, but end-to-end latency still depends on the RPM, Ethernet, host scheduling, Docker, and each receiving application.

## Reliability behavior

- One persistent upstream connection is shared by all consumers.
- The proxy retries an unavailable RPM every two seconds.
- All consumers are disconnected after upstream loss so they reopen a clean TCP session.
- No historical data is buffered while the RPM or a consumer is disconnected.
- A newly connected consumer starts with the next live bytes; it may join in the middle of an RPM message and must resynchronize using the RPM protocol's framing.
- A slow consumer is dropped when its bounded queue fills; current consumers continue.
- Docker restarts the service after crashes and host reboots.
- An event-loop watchdog forces a restart if the proxy becomes unresponsive.
- The status API is available only from the host at `http://127.0.0.1:9090`.

## Option A: Raspberry Pi

Use Raspberry Pi OS Lite 64-bit, wired Ethernet, Docker Engine with Compose v2, `iproute2`, and `iputils-arping`. A Pi with eMMC or a high-endurance SSD is preferable to a consumer microSD card for unattended use.

Run the installation from a local keyboard/display because applying `192.168.2.2/24` can disconnect SSH:

```bash
cd /path/to/rpm-edge-proxy
sudo ./scripts/install-pi.sh --apply-network --interface eth0
```

The installer checks for another owner of `192.168.2.2`, checks for the RPM at `192.168.2.3`, creates a persistent NetworkManager profile, builds or loads the container image, and starts the service. Reboot once and verify:

```bash
sudo /opt/rpm-edge-proxy/scripts/verify.sh eth0
```

## Option B: Windows 11 with Docker Desktop

Docker Desktop must be running Linux containers. A dedicated wired RPM adapter is strongly recommended so changing its static address cannot disrupt the Windows management or internet connection. Open PowerShell as Administrator in this directory.

Windows can have both `192.168.2.2/24` and `192.168.3.99/24` on the same Ethernet adapter when the RPM and camera are on the same physical Ethernet segment. The proxy uses `.2.2` for the RPM path; `.3.99` remains available for OSCAR to reach camera `.3.2`. Adding `.2.2` does not replace an existing static `.3.99` address. Do not configure a default gateway on either isolated subnet unless the site network design specifically requires one.

If the selected Ethernet adapter already owns `192.168.2.2/24`:

```powershell
.\scripts\install-windows.ps1 -InterfaceAlias "Ethernet"
```

If the address is not configured, have the script perform a conflict check and add it:

```powershell
.\scripts\install-windows.ps1 -InterfaceAlias "Ethernet" -ConfigureNetwork
```

`-ConfigureNetwork` assigns a manual address and Windows may disable DHCP on that adapter. Use it only for the dedicated RPM LAN; configure the address manually if the adapter also carries other traffic.

The script also creates an inbound Windows Firewall rule for TCP `1600` from the local subnet, builds the image, and starts the proxy. Verify later with:

```powershell
.\scripts\verify-windows.ps1 -InterfaceAlias "Ethernet"
```

For unattended use, apply the reversible Windows server profile after OSCAR and the proxy containers are running:

```powershell
.\scripts\configure-windows-server.ps1 -InterfaceAlias "Ethernet"
```

This prevents sleep and browser/Windows power throttling, defers disruptive update installation, applies container restart policies, and installs a guarded Docker recovery watchdog. Firmware auto-power-on and secure automatic Windows sign-in still require one-time configuration. Follow [WINDOWS-SERVER.md](WINDOWS-SERVER.md) before enabling it.

The separate CAS computer should use `192.168.2.2:1600`. An OSCAR process running directly on Windows can use the same address. If OSCAR runs in another Docker Compose project on the same host, the most direct container-to-container path is to attach its service to `rpm-edge-proxy-network` and use `rpm-edge-proxy:1600`:

```yaml
services:
  oscar:
    networks:
      - default
      - rpm_proxy

networks:
  rpm_proxy:
    external: true
    name: rpm-edge-proxy-network
```

If OSCAR accepts only an IP address, first test `192.168.2.2:1600` from its container; Docker Desktop network behavior can vary by release.

## Operations

```bash
# Process is alive
curl http://127.0.0.1:9090/healthz

# HTTP 200 only while connected to the RPM
curl --fail http://127.0.0.1:9090/readyz

# Client count, byte counters, reconnects, and errors
curl http://127.0.0.1:9090/status

# Recent bounded logs
docker logs --tail 200 rpm-edge-proxy

# Deliberate restart
docker restart rpm-edge-proxy
```

`active_clients` should match the number of currently connected receiving applications. `bytes_from_upstream` counts each byte once; `bytes_to_clients` counts each delivered copy, so it grows roughly N times faster with N consumers.

See [RUNBOOK.md](RUNBOOK.md) for commissioning and failure tests and [SECURITY.md](SECURITY.md) for production hardening notes.

## Configuration

The active configuration is [config/config.json](config/config.json). The application listens on all interfaces *inside* the container; Compose publishes TCP `1600` only on host address `192.168.2.2` and publishes the status port only on host loopback.

For a temporary bench test on a host that does not own `192.168.2.2`, override only the Docker bind address:

```bash
RPM_PROXY_BIND_IP=127.0.0.1 docker compose up --build
```

That override does not change the real upstream RPM address.

Validate source, configuration, tests, and shell syntax without starting Docker:

```bash
./scripts/validate.sh
```

## Offline images

On an internet-connected machine with Docker Buildx:

```bash
./scripts/build-offline-bundles.sh 2.0.0
```

This creates ARM64 and AMD64 Linux-container archives plus SHA-256 checksums under `dist/`. The Pi installer automatically loads the ARM64 archive when present. On Windows, load the AMD64 archive with `docker load --input <archive>` and run `install-windows.ps1 -NoBuild`.
