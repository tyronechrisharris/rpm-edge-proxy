#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd "$script_dir/.." && pwd)"
version="${1:-1.1.0}"
image="rpm-edge-proxy"
dist_dir="$project_dir/dist"

mkdir -p "$dist_dir"

build_bundle() {
  local platform="$1"
  local architecture="$2"
  local archive="$dist_dir/${image}-${version}-linux-${architecture}.tar"
  docker buildx build \
    --platform "$platform" \
    --build-arg "VERSION=$version" \
    --tag "${image}:${version}" \
    --output "type=docker,dest=$archive" \
    "$project_dir"
  gzip -f "$archive"
}

build_bundle "linux/arm64" "arm64"

if command -v sha256sum >/dev/null 2>&1; then
  (cd "$dist_dir" && sha256sum ./*.tar.gz > SHA256SUMS)
else
  (cd "$dist_dir" && shasum -a 256 ./*.tar.gz > SHA256SUMS)
fi

echo "Offline image bundles are in $dist_dir"
