#!/usr/bin/env bash
set -euo pipefail

source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
install_dir="/opt/rpm-edge-proxy"
network_interface="eth0"
apply_network="false"
enable_watchdog="true"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --interface)
      network_interface="${2:?--interface requires a value}"
      shift 2
      ;;
    --apply-network)
      apply_network="true"
      shift
      ;;
    --no-hardware-watchdog)
      enable_watchdog="false"
      shift
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo $0 --apply-network [--interface eth0]" >&2
  exit 2
fi
if [[ "$apply_network" != "true" ]]; then
  echo "Network activation can disconnect SSH." >&2
  echo "Run from a local console with --apply-network after both RPMs are readdressed." >&2
  exit 2
fi
for command_name in docker nmcli arping ip; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Missing required command: $command_name" >&2
    exit 2
  fi
done
if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is required." >&2
  exit 2
fi

"$source_dir/scripts/preflight.sh" "$network_interface"

if [[ "$source_dir" != "$install_dir" ]]; then
  install -d -m 0755 "$install_dir"
  cp -a "$source_dir/." "$install_dir/"
fi
find "$install_dir/scripts" -type f -name '*.sh' -exec chmod 0755 {} +

systemctl enable --now docker
cd "$install_dir"
offline_archive="$install_dir/dist/rpm-edge-proxy-1.1.0-linux-arm64.tar.gz"
if ! docker image inspect rpm-edge-proxy:1.1.0 >/dev/null 2>&1 \
  && [[ -f "$offline_archive" ]]; then
  docker load --input "$offline_archive"
fi
if ! docker image inspect rpm-edge-proxy:1.1.0 >/dev/null 2>&1; then
  docker compose build
fi

install -d -m 0755 /etc/NetworkManager/dispatcher.d
install -m 0755 "$install_dir/deploy/90-rpm-edge-proxy-arp-announce" \
  /etc/NetworkManager/dispatcher.d/90-rpm-edge-proxy-arp-announce
"$install_dir/scripts/configure-network.sh" "$network_interface"

if [[ "$enable_watchdog" == "true" && ( -e /dev/watchdog0 || -e /dev/watchdog ) ]]; then
  install -d -m 0755 /etc/systemd/system.conf.d
  install -m 0644 "$install_dir/deploy/20-hardware-watchdog.conf" \
    /etc/systemd/system.conf.d/20-hardware-watchdog.conf
  echo "Hardware watchdog configuration installed; it becomes active after reboot."
elif [[ "$enable_watchdog" == "true" ]]; then
  echo "WARNING: /dev/watchdog is unavailable; hardware watchdog was not enabled." >&2
fi

docker compose up --detach --no-build --remove-orphans

sleep 5
"$install_dir/scripts/verify.sh" "$network_interface"

echo "Installation complete. Reboot once, then run:"
echo "  sudo $install_dir/scripts/verify.sh $network_interface"
