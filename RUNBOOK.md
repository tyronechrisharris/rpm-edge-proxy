# Commissioning and Recovery Runbook

## Before startup

1. Confirm the real RPM is configured as `192.168.2.3/24` and accepts TCP on port `1600`.
2. Confirm no other device owns `192.168.2.2`.
3. Confirm the proxy host has wired address `192.168.2.2/24`.
4. If Windows also reaches camera `192.168.3.2`, confirm the same adapter retains `192.168.3.99/24` and that both addresses have unique owners.
5. Keep the Pi and Windows deployments mutually exclusive; there must be exactly one proxy host at `.2`.
6. Confirm every external consumer is configured to connect to `192.168.2.2:1600`, not directly to `.3`.

On a Pi, run:

```bash
sudo ./scripts/preflight.sh eth0
sudo ./scripts/verify.sh eth0
```

On Windows, run from elevated PowerShell:

```powershell
.\scripts\verify-windows.ps1 -InterfaceAlias "Ethernet"
```

## Acceptance test

1. Open `http://127.0.0.1:9090/status` on the proxy host.
2. Confirm `upstream_connected` is `true` and `/readyz` returns HTTP 200.
3. Start two receiving applications on two separate computers.
4. Confirm `active_clients` becomes `2`.
5. Generate a known RPM event and confirm both applications receive the same complete event in the correct order.
6. Confirm `bytes_from_upstream` and `bytes_to_clients` increase. With two active consumers, the latter should grow at about twice the former.
7. Record observed event timestamps at the RPM/proxy and both consumers. Establish the acceptable site-specific latency limit before production use.

Do not use `ping` alone as acceptance evidence. It proves IP reachability, not a live TCP feed or byte-for-byte fan-out.

## Required failure tests

| Test | Action | Required result |
| --- | --- | --- |
| Proxy process crash | `docker kill rpm-edge-proxy` | Docker restarts it; the RPM and consumers reconnect. |
| RPM cable interruption | Unplug the RPM Ethernet cable for 60 seconds, then reconnect. | Readiness becomes degraded, then returns without operator action. |
| Proxy cable interruption | Unplug the proxy Ethernet cable for 60 seconds, then reconnect. | The listener and upstream recover; consumers reconnect. |
| RPM power cycle | Power-cycle the RPM. | The proxy reconnects automatically after TCP port `1600` returns. |
| Proxy host reboot | Reboot the Pi or Windows host. | Address `.2` and the container return automatically. On Windows, confirm Docker Desktop is configured to start at sign-in or as required by site operations. |
| Slow consumer | Pause one receiver or block its reads while other receivers run. | The slow connection is eventually dropped; other receivers continue. |
| Multiple consumers | Run all intended receivers simultaneously for at least one hour. | No missing/reordered bytes, unbounded memory growth, or repeated reconnects. |

## Status interpretation

- `/healthz = 200`: the process and event loop can answer locally.
- `/readyz = 200`: the required RPM connection is active.
- `/readyz = 503`: the process is alive but the RPM is disconnected; retries continue every two seconds.
- `active_clients = 0`: no receiving application is currently connected.
- Increasing `bytes_from_upstream` with `active_clients = 0`: live RPM data is arriving but nobody consumes it.
- `bytes_to_clients` not increasing for one receiver: inspect that receiver and its network path.
- Increasing `dropped_clients`: one or more consumers cannot keep up.
- Increasing `errors` or `reconnects`: inspect cabling, power, duplicate IPs, firewall state, and the RPM TCP service.

## Recovery order

1. Check `/status` and `docker logs --tail 200 rpm-edge-proxy`.
2. Confirm unique IP-to-MAC mappings for `.2` and `.3`.
3. Confirm the RPM accepts TCP at `192.168.2.3:1600`.
4. Confirm the proxy host owns `192.168.2.2/24`.
5. Confirm the container is running and host TCP `192.168.2.2:1600` is reachable.
6. Restart only the proxy with `docker restart rpm-edge-proxy`.
7. Reboot the host only if the process and network checks do not recover it.

No data is retained during an outage. Any regulatory or operational requirement for guaranteed event retention must be met by the RPM or receiving systems, not this relay.

## Pi rollback

From a local console:

```bash
docker stop rpm-edge-proxy
sudo nmcli connection down rpm-edge-proxy
nmcli connection show
sudo nmcli connection up "<previous connection name>"
```

## Windows rollback

From elevated PowerShell, stop the container and remove only the address/rule installed for this proxy:

```powershell
docker stop rpm-edge-proxy
Remove-NetFirewallRule -Name "RPMEdgeProxy-TCP-1600"
Get-NetIPAddress -IPAddress 192.168.2.2 | Remove-NetIPAddress -Confirm
```

Do not remove `192.168.2.2` if it was already the Windows host's address before this deployment.
