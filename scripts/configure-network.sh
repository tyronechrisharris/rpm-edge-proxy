#!/usr/bin/env bash
set -euo pipefail

network_interface="${1:-eth0}"
network_profile="rpm-edge-proxy"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo $0 [interface]" >&2
  exit 2
fi
if ! command -v nmcli >/dev/null 2>&1; then
  echo "NetworkManager/nmcli is required." >&2
  exit 2
fi
if ! nmcli general status >/dev/null 2>&1; then
  echo "NetworkManager is not running." >&2
  exit 2
fi
if ! ip link show dev "$network_interface" >/dev/null 2>&1; then
  echo "Network interface does not exist: $network_interface" >&2
  exit 2
fi

if ! nmcli --terse --fields NAME connection show | grep -Fqx "$network_profile"; then
  nmcli connection add type ethernet ifname "$network_interface" con-name "$network_profile"
fi

nmcli connection modify "$network_profile" \
  connection.interface-name "$network_interface" \
  connection.autoconnect yes \
  connection.autoconnect-priority 100 \
  ipv4.method manual \
  ipv4.addresses "172.26.0.51/24,172.26.0.33/24,172.26.0.65/24" \
  ipv4.gateway "" \
  ipv4.dns "" \
  ipv4.ignore-auto-dns yes \
  ipv4.never-default yes \
  ipv6.method disabled

echo "Activating $network_profile on $network_interface. An SSH session on this interface may disconnect."
nmcli connection up "$network_profile" ifname "$network_interface"
ip -o -4 address show dev "$network_interface"
