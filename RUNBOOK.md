# Commissioning and Recovery Runbook

## Acceptance checks before connecting the CAS

1. Confirm the Export RPM is `172.26.0.32/24` and the Import RPM is `172.26.0.64/24`.
2. Confirm `172.26.0.33` and `172.26.0.65` are unused before installing the Pi profile.
3. Confirm the Pi reports all three addresses on the same wired interface:

   ```bash
   ip -4 address show dev eth0
   ```

4. Confirm the two exact listeners exist:

   ```bash
   ss -ltn | grep ':1600'
   ```

5. Confirm both upstream connections are ready:

   ```bash
   curl --fail http://127.0.0.1:9090/readyz
   curl http://127.0.0.1:9090/status
   ```

6. Connect the CAS and confirm the `active_clients` and `bytes_to_clients` counters increase for the correct lane.
7. Compare several RPM messages at the RPM side and CAS side to confirm byte-for-byte and lane-correct delivery.

## Required failure tests

Perform these tests before declaring the gateway operational:

| Test | Action | Required result |
| --- | --- | --- |
| Proxy crash | `docker kill rpm-edge-proxy` | Docker restarts the container automatically; both RPM services reconnect. |
| RPM link interruption | Unplug one RPM network cable for 60 seconds, then reconnect it. | Only that service reports degraded; it reconnects without operator action. |
| Pi link interruption | Unplug the Pi Ethernet cable for 60 seconds, then reconnect it. | Both services recover and CAS clients reconnect. |
| RPM power cycle | Power-cycle each RPM separately. | The affected service reconnects automatically after the RPM TCP listener returns. |
| Pi power loss | Remove Pi power for at least 10 seconds, then restore it. | Network aliases and the container return automatically after boot. |
| Slow/stalled CAS | Disconnect or stop one CAS client. | RPM acquisition and any other CAS client continue; no unbounded queue grows. |
| Repeated reboot | Reboot the Pi five times. | All five boots restore `.33`, `.65`, and both proxy paths without manual action. |

Record recovery time for each test and retain the `/status` output and recent logs.

## Status interpretation

- `healthz = 200`: the proxy process and event loop can answer locally.
- `readyz = 200`: both required persistent RPM connections are active.
- `readyz = 503`: one or both RPMs are disconnected; the process is still retrying automatically.
- `active_clients = 0`: no CAS currently consumes that lane.
- Increasing `bytes_from_upstream` with zero `bytes_to_clients`: RPM data is arriving but no CAS is connected.
- Increasing `errors` or `reconnects`: inspect cabling, switch ports, RPM power, duplicate addresses, and link negotiation.
- Increasing `dropped_clients`: a CAS client is too slow to consume real-time data.

## Recovery order

1. Run `sudo /opt/rpm-edge-proxy/scripts/verify.sh eth0`.
2. Inspect `curl http://127.0.0.1:9090/status`.
3. Inspect `docker logs --tail 200 rpm-edge-proxy`.
4. Confirm all five relevant hosts have unique IP-to-MAC mappings with `ip neigh show dev eth0`.
5. Confirm `.32` and `.64` respond on the Ethernet segment.
6. Confirm the container is listening specifically on `.33:1600` and `.65:1600`.
7. Restart only the proxy with `docker restart rpm-edge-proxy`.
8. Reboot the Pi only if the process and network checks do not recover it.

Do not reassign an RPM to `.33` or `.65` as a troubleshooting shortcut while the Pi is connected.

To roll back the Pi network profile from a local console, stop the proxy, deactivate `rpm-edge-proxy`, and reactivate the previous NetworkManager connection:

```bash
docker stop rpm-edge-proxy
sudo nmcli connection down rpm-edge-proxy
nmcli connection show
sudo nmcli connection up "<previous connection name>"
```

## Planned maintenance

Before changing the image or configuration:

1. Copy `/opt/rpm-edge-proxy/config/config.json` and record the current image ID.
2. Validate the new release on a bench with simulated RPM streams.
3. Schedule a cutover window and notify CAS operators.
4. Load the pinned image locally; do not use a floating `latest` tag.
5. Run the complete failure-test table after the change.
