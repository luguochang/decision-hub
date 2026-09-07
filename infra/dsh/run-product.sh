#!/usr/bin/env bash
set -euo pipefail

# Single-owner product launcher. It starts the Hub control plane and workers in
# one compose project, then exposes the pinned official DSH Web as the only
# user-facing URL. Replay remains an explicit acceptance command.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
compose_file="$repo_root/compose.yaml"
dsh_port="${DSH_PRODUCT_PORT:-3080}"
api_port="${DECISION_HUB_API_PORT:-8000}"
mcp_port="${DECISION_HUB_RESEARCH_MCP_PORT:-8002}"
compose_project="${DECISION_HUB_COMPOSE_PROJECT:-decision-hub-product-${api_port}}"
host_key="${DECISION_HUB_DSH_HOST_KEY:-decision-hub-local-host-key}"
callback_key="${DECISION_HUB_DSH_CALLBACK_KEY:-${DECISION_HUB_DSH_CALLBACK_SECRET:-decision-hub-local-callback-key}}"
research_tool_key="${DECISION_HUB_DSH_RESEARCH_TOOL_KEY:-decision-hub-local-research-tool-key}"
workspace_cwd="${DECISION_HUB_DSH_WORKSPACE_CWD:-$repo_root}"
credential_file="$repo_root/data/dsh-live/.env"
if [[ -z "${DECISION_HUB_DSH_WORKSPACE_CWD:-}" ]]; then
  workspace_cwd="$repo_root/data/dsh-live/Crypto Macro Trader"
fi
if [[ ! "$compose_project" =~ ^[a-z0-9][a-z0-9_-]{0,62}$ ]]; then
  echo "DECISION_HUB_COMPOSE_PROJECT must be a lowercase Docker Compose project name" >&2
  exit 2
fi
compose_cmd=(docker compose --project-name "$compose_project")
if [[ -f "$credential_file" ]]; then
  # Compose uses the same gitignored Provider settings as official DSH Web.
  # Values are interpolated directly into service environments and are never
  # printed, sourced as shell code or copied into the repository.
  compose_cmd+=(--env-file "$credential_file")
fi
compose_cmd+=(-f "$compose_file")

if [[ ! -f "$compose_file" ]]; then
  echo "product compose file is missing: $compose_file" >&2
  exit 2
fi
mkdir -p "$workspace_cwd"
if [[ -n "${DSH_REPLAY_PLUGIN:-}" || -n "${DSH_SNAPSHOT_FILE:-}" || -n "${DSH_SNAPSHOT_OVERRIDE:-}" ]]; then
  echo "run-product.sh refuses replay configuration; use tools/dsh_native_acceptance.py for replay" >&2
  exit 2
fi

export DECISION_HUB_DSH_HOST_KEY="$host_key"
export DECISION_HUB_DSH_CALLBACK_KEY="$callback_key"
export DECISION_HUB_DSH_CALLBACK_SECRET="$callback_key"
export DECISION_HUB_DSH_RESEARCH_TOOL_KEY="$research_tool_key"
export DECISION_HUB_DSH_WORKSPACE_CWD="$workspace_cwd"
export DECISION_HUB_DSH_RUNTIME_MODE=live
export DECISION_HUB_DSH_WEB_URL="${DECISION_HUB_DSH_WEB_URL:-http://127.0.0.1:$dsh_port}"
# The Web process runs on the host while the research worker runs in Compose.
# compose.yaml maps this internal URL to host.docker.internal.
export DECISION_HUB_DSH_WEB_INTERNAL_URL="${DECISION_HUB_DSH_WEB_INTERNAL_URL:-http://host.docker.internal:$dsh_port}"
export DECISION_HUB_DSH_HOST_SECRET="$host_key"
export DECISION_HUB_DSH_WORKSPACE_CWD_CONTAINER="${DECISION_HUB_DSH_WORKSPACE_CWD_CONTAINER:-/app}"
export DECISION_HUB_API_PORT="$api_port"
export DECISION_HUB_API_URL="${DECISION_HUB_API_URL:-http://127.0.0.1:$api_port}"
export DECISION_HUB_DESK_URL="${DECISION_HUB_DESK_URL:-$DECISION_HUB_API_URL}"
export DECISION_HUB_RESEARCH_RUNTIME=dsh-web
export DECISION_HUB_RESEARCH_EXECUTION_MODE=live
research_capabilities="${DECISION_HUB_RESEARCH_CAPABILITIES:-official.macro,market.cross_asset,market.crypto_derivatives,web.fetch}"
research_capabilities="${research_capabilities//[[:space:]]/}"
if [[ -z "$research_capabilities" ]]; then
  echo "live product requires at least one approved research capability" >&2
  exit 2
fi
if [[ ",$research_capabilities," == *",replay.research,"* ]]; then
  echo "live product refuses replay.research; use the isolated replay acceptance command" >&2
  exit 2
fi
export DECISION_HUB_RESEARCH_CAPABILITIES="$research_capabilities"
export DECISION_HUB_SOURCES_ENABLED="${DECISION_HUB_SOURCES_ENABLED:-1}"
export DECISION_HUB_RESEARCH_MCP_PORT="$mcp_port"
export DECISION_HUB_RESEARCH_MCP_URL="${DECISION_HUB_RESEARCH_MCP_URL:-http://127.0.0.1:$mcp_port/mcp}"
export DECISION_HUB_RESEARCH_TOOL_URL="${DECISION_HUB_RESEARCH_TOOL_URL:-http://127.0.0.1:$mcp_port/decision-hub/v1/research-capabilities/execute}"
# The compose service has its own in-container default for the evaluation case.
# Do not pass a host absolute path into the container unless the caller explicitly
# supplies a container-readable path.

if [[ -z "${DEEPSEEK_API_KEY:-}" && -z "${OPENAI_API_KEY:-}" && ! -f "$credential_file" ]]; then
  echo "No live Provider credential found. Configure DEEPSEEK_API_KEY/OPENAI_API_KEY or data/dsh-live/.env" >&2
  exit 78
fi

echo "Building Decision Hub product images from the current worktree..." >&2
"${compose_cmd[@]}" build hub-api hub-realtime-worker hub-evolution-worker research-mcp hub-research-worker >&2

echo "Starting Decision Hub control plane (API, event/evolution workers, research gateway)..." >&2
# Research execution is deliberately held behind the DSH Host readiness
# barrier below. Realtime admission may durably queue Runs while the official
# Web boots, but no Run can be claimed before DSH can call back into this Hub.
"${compose_cmd[@]}" up -d --no-build --force-recreate hub-api hub-realtime-worker hub-evolution-worker research-mcp >&2

cleanup() {
  status=$?
  if [[ -n "${dsh_pid:-}" ]] && kill -0 "$dsh_pid" >/dev/null 2>&1; then
    kill "$dsh_pid" >/dev/null 2>&1 || true
    wait "$dsh_pid" >/dev/null 2>&1 || true
  fi
  if [[ "${DSH_PRODUCT_KEEP_SERVICES:-0}" != "1" ]]; then
    "${compose_cmd[@]}" stop hub-research-worker research-mcp hub-evolution-worker hub-realtime-worker hub-api >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap cleanup INT TERM EXIT

for _ in {1..30}; do
  if curl --fail --silent "$DECISION_HUB_API_URL/health/ready" >/dev/null; then
    break
  fi
  sleep 1
done
if ! curl --fail --silent "$DECISION_HUB_API_URL/health/ready" >/dev/null; then
  echo "Decision Hub API did not become ready" >&2
  exit 1
fi

# A generic health response is insufficient: an old image can answer 200 while
# lacking the product routes used by the current DSH extension. Verify the
# canonical Inbox projection before starting the only user-facing Web.
product_inbox_url="$DECISION_HUB_API_URL/v1/research/inbox?limit=1"
product_inbox_payload="$(curl --fail --silent --show-error "$product_inbox_url" || true)"
if ! printf '%s' "$product_inbox_payload" | python3 -c 'import json,sys; payload=json.load(sys.stdin); raise SystemExit(0 if payload.get("schema_version") == "research-inbox-view.v1" else 1)' 2>/dev/null; then
  echo "product API contract did not become ready (expected research-inbox-view.v1)" >&2
  exit 1
fi

for _ in {1..30}; do
  if python3 - "$mcp_port" <<'PY'
import socket
import sys

with socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=1):
    pass
PY
  then
    break
  fi
  sleep 1
done
if ! python3 - "$mcp_port" <<'PY'
import socket
import sys

with socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=1):
    pass
PY
then
  echo "research MCP did not become ready" >&2
  exit 1
fi

echo "Starting official DSH Web; this is the only user-facing URL." >&2
web_log="$(mktemp -t decision-hub-dsh-web.XXXXXX.log)"
cookie_jar="$(mktemp -t decision-hub-dsh-cookie.XXXXXX)"
"$repo_root/infra/dsh/run-live-web.sh" --port "$dsh_port" >"$web_log" 2>&1 &
dsh_pid=$!

dsh_url=""
for _ in {1..120}; do
  if ! kill -0 "$dsh_pid" >/dev/null 2>&1; then
    echo "official DSH Web exited before publishing its URL" >&2
    sed -n '1,120p' "$web_log" >&2
    exit 1
  fi
  dsh_url="$(sed -nE 's/^dsh web: (http:\/\/127\.0\.0\.1:[0-9]+\/\?token=[^[:space:]]+).*$/\1/p' "$web_log" | tail -n 1)"
  if [[ -n "$dsh_url" ]]; then
    break
  fi
  sleep 1
done
if [[ -z "$dsh_url" ]]; then
  echo "official DSH Web did not publish an authenticated URL" >&2
  sed -n '1,160p' "$web_log" >&2
  exit 1
fi

# Authenticate one request and register the product workspace through the
# official DSH workspace controller. The upstream UI remains the owner of the
# workspace/session navigation and renders the basename as its title.
curl --fail --silent --show-error --cookie-jar "$cookie_jar" "$dsh_url" >/dev/null
workspace_payload="$(printf '%s' "$workspace_cwd" | python3 -c 'import json,sys; print(json.dumps({"type":"client-request","rpcId":"decision-hub-workspace-register","method":"workspace/create","payload":{"args":{"request":{"path":sys.stdin.read()}}}}))')"
workspace_response="$(curl --fail --silent --show-error --cookie "$cookie_jar" \
  -H 'content-type: application/json' \
  -X POST "http://127.0.0.1:$dsh_port/api/workspace/create" \
  --data "$workspace_payload")"
if ! printf '%s' "$workspace_response" | python3 -c 'import json,sys; payload=json.load(sys.stdin); raise SystemExit(0 if payload.get("result", {}).get("ok") is True else 1)'; then
  echo "official DSH workspace registration failed" >&2
  printf '%s\n' "$workspace_response" >&2
  exit 1
fi

host_readiness_url="http://127.0.0.1:$dsh_port/decision-hub/v1/readiness"
for _ in {1..30}; do
  readiness="$(curl --fail --silent --show-error \
    -H "X-Decision-Hub-Host-Key: $host_key" "$host_readiness_url" || true)"
  if printf '%s' "$readiness" | python3 -c 'import json,sys; payload=json.load(sys.stdin); raise SystemExit(0 if payload.get("ready") is True else 1)' 2>/dev/null; then
    break
  fi
  sleep 1
done
if ! printf '%s' "$(curl --fail --silent --show-error \
  -H "X-Decision-Hub-Host-Key: $host_key" "$host_readiness_url")" | \
  python3 -c 'import json,sys; payload=json.load(sys.stdin); raise SystemExit(0 if payload.get("ready") is True else 1)'; then
  echo "Decision Hub DSH Host did not become ready" >&2
  exit 1
fi

echo "Starting research worker after the DSH Host readiness barrier..." >&2
"${compose_cmd[@]}" up -d --no-deps --no-build --force-recreate hub-research-worker >&2
research_worker_running=false
for _ in {1..30}; do
  if "${compose_cmd[@]}" ps --status running --services | grep -Fxq "hub-research-worker"; then
    research_worker_running=true
    break
  fi
  sleep 1
done
if [[ "$research_worker_running" != true ]]; then
  echo "research worker did not enter running state" >&2
  "${compose_cmd[@]}" logs --tail=120 hub-research-worker >&2 || true
  exit 1
fi

printf 'DSH_URL=%s\n' "$dsh_url"
printf 'COMPOSE_PROJECT=%s\n' "$compose_project"
printf 'TRADER_WORKSPACE=%s\n' "$workspace_cwd"
printf 'Product is ready. Keep this process running while using the DSH Web URL.\n'
wait "$dsh_pid"
