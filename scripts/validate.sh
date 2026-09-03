#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd "$script_dir/.." && pwd)"

PYTHONPATH="$project_dir" python3 -m unittest discover -s "$project_dir/tests" -v
PYTHONPATH="$project_dir" python3 -m cas_proxy \
  --config "$project_dir/config/config.json" --validate

for script in "$project_dir"/scripts/*.sh; do
  bash -n "$script"
done
