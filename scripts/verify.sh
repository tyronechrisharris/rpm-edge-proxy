#!/usr/bin/env bash
set -euo pipefail

network_interface="${1:-eth0}"
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

for address in 192.168.2.2; do
  if ! ip -o -4 address show dev "$network_interface" | awk '{print $4}' | cut -d/ -f1 | grep -Fqx "$address"; then
    echo "FAIL: $address is not assigned to $network_interface" >&2
    exit 1
  fi
done

docker compose --file "$project_dir/compose.yaml" ps
container_status="$(docker inspect --format '{{.State.Status}}' rpm-edge-proxy 2>/dev/null || true)"
health_status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' rpm-edge-proxy 2>/dev/null || true)"
echo "Container state: ${container_status:-missing}; health: ${health_status:-missing}"
if [[ "$container_status" != "running" ]]; then
  docker compose --file "$project_dir/compose.yaml" logs --tail 100 proxy
  exit 1
fi

published_listener="$(docker port rpm-edge-proxy 1600/tcp 2>/dev/null || true)"
if ! grep -Fqx "192.168.2.2:1600" <<<"$published_listener"; then
  echo "FAIL: container port 1600 is not published as 192.168.2.2:1600" >&2
  echo "Published value: ${published_listener:-none}" >&2
  exit 1
fi

if command -v timeout >/dev/null 2>&1; then
  if ! timeout 3 bash -c 'exec 3<>/dev/tcp/192.168.2.2/1600'; then
    echo "FAIL: cannot open the proxy listener at 192.168.2.2:1600" >&2
    exit 1
  fi
fi

if command -v curl >/dev/null 2>&1; then
  echo "Readiness:"
  if ! curl --fail --silent --show-error http://127.0.0.1:9090/readyz; then
    echo "The RPM upstream connection is not ready. Current status:" >&2
    curl --silent --show-error http://127.0.0.1:9090/status || true
    exit 1
  fi
  curl --silent --show-error http://127.0.0.1:9090/status
fi

echo "Listening sockets:"
ss -ltnp | awk 'NR == 1 || /192\.168\.2\.2:1600/'
echo "Verification passed."
