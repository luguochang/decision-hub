#!/usr/bin/env bash
set -euo pipefail

# Live is a separate entry point on purpose. It never accepts replay fixtures,
# and it fails before DSH boots when no compatible credential is available.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Keep live state and credentials separate from replay acceptance state. The
# directory is gitignored and the official DSH settings/credential seams read
# its `.env`/`settings.yaml` without copying secrets into source or logs.
export DSH_WEB_HOME="${DSH_WEB_HOME:-$repo_root/data/dsh-live}"

if [[ -n "${DSH_REPLAY_PLUGIN:-}" || -n "${DSH_SNAPSHOT_FILE:-}" || -n "${DSH_SNAPSHOT_OVERRIDE:-}" ]]; then
  echo "live DSH refuses replay configuration; unset DSH_REPLAY_PLUGIN/DSH_SNAPSHOT_FILE/DSH_SNAPSHOT_OVERRIDE" >&2
  exit 2
fi

# DSH's official provider is named DeepSeek, while this local route is served
# by llm-pi-ai over the gateway's OpenAI Responses contract. Explicit DeepSeek
# variables win; the OpenAI names are a convenience for a compatible gateway
# and never get persisted.
if [[ -z "${DEEPSEEK_API_KEY:-}" && -n "${OPENAI_API_KEY:-}" ]]; then
  export DEEPSEEK_API_KEY="$OPENAI_API_KEY"
fi
if [[ -z "${DEEPSEEK_BASE_URL:-}" && -n "${OPENAI_BASE_URL:-}" ]]; then
  export DEEPSEEK_BASE_URL="$OPENAI_BASE_URL"
fi
credential_file="$DSH_WEB_HOME/.env"
credential_file_configured=false
if [[ -f "$credential_file" ]] && grep -Eq '^(DEEPSEEK_API_KEY|OPENAI_API_KEY)=[^[:space:]]' "$credential_file"; then
  credential_file_configured=true
fi
if [[ -z "${DEEPSEEK_API_KEY:-}" && -z "${OPENAI_API_KEY:-}" && "$credential_file_configured" != true ]]; then
  echo "live DSH requires a configured DEEPSEEK_API_KEY or OPENAI_API_KEY (process env or $credential_file)" >&2
  exit 78
fi

export DECISION_HUB_DSH_RUNTIME_MODE=live
# Direct Live launches must provide the same audited research-tool settings as
# run-product.sh. Without these defaults the official DSH Web can boot while
# the decision-research preset fails to mount with an opaque HTTP 500.
export DECISION_HUB_DSH_RESEARCH_TOOL_KEY="${DECISION_HUB_DSH_RESEARCH_TOOL_KEY:-decision-hub-local-research-tool-key}"
research_mcp_port="${DECISION_HUB_RESEARCH_MCP_PORT:-8002}"
export DECISION_HUB_RESEARCH_TOOL_URL="${DECISION_HUB_RESEARCH_TOOL_URL:-http://127.0.0.1:$research_mcp_port/decision-hub/v1/research-capabilities/execute}"
printf 'dsh decision-hub live preflight: provider credential present; starting interactive Web\n' >&2
exec "$repo_root/infra/dsh/run-web.sh" "$@"
