#!/usr/bin/env bash
set -euo pipefail

# Stop only product services and preserve all named data volumes. The DSH Web
# process is foreground-owned by run-product.sh and is stopped by SIGTERM.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
compose_file="$repo_root/compose.yaml"

if [[ ! -f "$compose_file" ]]; then
  echo "product compose file is missing: $compose_file" >&2
  exit 2
fi

docker compose -f "$compose_file" stop \
  hub-research-worker research-mcp hub-evolution-worker hub-realtime-worker hub-api
echo "Decision Hub product services stopped; persistent volumes were preserved."
