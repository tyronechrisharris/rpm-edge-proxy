#!/usr/bin/env bash
set -euo pipefail

network_interface="${1:-eth0}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run preflight as root: sudo $0 [interface]" >&2
  exit 2
fi
for command_name in ip arping; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Missing required command: $command_name" >&2
    echo "On Raspberry Pi OS install iproute2 and iputils-arping." >&2
    exit 2
  fi
done
if ! ip link show dev "$network_interface" >/dev/null 2>&1; then
  echo "Network interface does not exist: $network_interface" >&2
  exit 2
fi

address_is_local() {
  local address="$1"
  ip -o -4 address show dev "$network_interface" | awk '{print $4}' | cut -d/ -f1 | grep -Fqx "$address"
}

check_alias_is_free() {
  local address="$1"
  if address_is_local "$address"; then
    echo "OK: $address is already assigned to $network_interface"
    return
  fi
  echo "Checking that replacement address $address is unused..."
  if arping -D -I "$network_interface" -c 3 -w 4 "$address" >/dev/null 2>&1; then
    echo "OK: $address is available"
  else
    echo "STOP: another device still answers for $address." >&2
    echo "Readdress the old RPM and clear this conflict before continuing." >&2
    exit 3
  fi
}

check_upstream_arp() {
  local address="$1"
  if arping -I "$network_interface" -c 2 -w 3 "$address" >/dev/null 2>&1; then
    echo "OK: upstream RPM responds to ARP at $address"
  else
    echo "WARNING: no ARP reply from upstream RPM $address" >&2
  fi
}

check_alias_is_free "172.26.0.33"
check_alias_is_free "172.26.0.65"
check_upstream_arp "172.26.0.32"
check_upstream_arp "172.26.0.64"

echo "Preflight complete. The installer assumes subnet 172.26.0.0/24."
