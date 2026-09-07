#!/usr/bin/env bash
set -euo pipefail

# OBS-01: exact-version official DSH OTel plugin canary. The default backend
# is a local metadata-only OTLP receiver so this path is keyless and works in
# CI. Use --jaeger to start the optional local Jaeger UI instead.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
backend="capture"
scenario="partial_failure"
for arg in "$@"; do
  case "$arg" in
    --jaeger) backend="jaeger" ;;
    --exporter-down) backend="down" ;;
    --scenario=success) scenario="success" ;;
    --scenario=partial_failure) scenario="partial_failure" ;;
    --scenario=insufficient_or_stale) scenario="insufficient_or_stale" ;;
    *) echo "usage: $0 [--jaeger|--exporter-down] [--scenario=success|partial_failure|insufficient_or_stale]" >&2; exit 2 ;;
  esac
done

if [[ ! -f "$repo_root/infra/dsh/observability/plugin.lock.json" ]]; then
  echo "observability lock is missing" >&2
  exit 1
fi
if [[ ! -f "$repo_root/.cache/dsh-upstream/source/apps/cli/lib/bin.js" ]]; then
  echo "pinned DSH build is missing; run infra/dsh/fetch-upstream.sh and build-upstream.sh" >&2
  exit 1
fi
if [[ ! -x "$repo_root/.venv/bin/python" ]]; then
  echo "project virtual environment is missing: $repo_root/.venv/bin/python" >&2
  exit 1
fi

run_root="${DSH_OBSERVABILITY_RUN_ROOT:-$repo_root/tmp/dsh-observability-canary}"
mkdir -p "$run_root"
capture_port="${DSH_OBSERVABILITY_CAPTURE_PORT:-$((RANDOM % 1000 + 19000))}"
capture_file="$run_root/otel-requests.jsonl"
: > "$capture_file"
jaeger_project="${DSH_OBSERVABILITY_COMPOSE_PROJECT:-decision-hub-observability}"
capture_pid=""

cleanup() {
  status=$?
  if [[ -n "$capture_pid" ]] && kill -0 "$capture_pid" >/dev/null 2>&1; then
    kill "$capture_pid" >/dev/null 2>&1 || true
    wait "$capture_pid" >/dev/null 2>&1 || true
  fi
  if [[ "$backend" == "jaeger" ]]; then
    docker compose --project-name "$jaeger_project" -f "$repo_root/infra/dsh/observability/jaeger.compose.yaml" down >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap cleanup EXIT INT TERM

if [[ "$backend" == "capture" ]]; then
  "$repo_root/.venv/bin/python" "$repo_root/tools/otel_capture.py" \
    --port "$capture_port" --output "$capture_file" &
  capture_pid=$!
  for _ in {1..30}; do
    if curl --fail --silent "http://127.0.0.1:$capture_port/health" >/dev/null; then break; fi
    sleep 0.2
  done
  curl --fail --silent "http://127.0.0.1:$capture_port/health" >/dev/null
  otel_endpoint="http://127.0.0.1:$capture_port"
elif [[ "$backend" == "jaeger" ]]; then
  docker compose --project-name "$jaeger_project" -f "$repo_root/infra/dsh/observability/jaeger.compose.yaml" up -d
  for _ in {1..60}; do
    if curl --fail --silent http://127.0.0.1:16686/ >/dev/null; then break; fi
    sleep 1
  done
  curl --fail --silent http://127.0.0.1:16686/ >/dev/null
  otel_endpoint="http://127.0.0.1:4318"
else
  # No listener on this loopback port: exporter errors must be isolated from
  # the DSH/Hub execution path and remain diagnostic-only.
  otel_endpoint="http://127.0.0.1:$((capture_port + 1))"
fi

echo "Running DSH $scenario with @loongsuite/dsh-plugin@0.1.2 -> $otel_endpoint" >&2
DSH_OBSERVABILITY_ENABLED=1 \
OTEL_SERVICE_NAME="decision-hub-dsh-canary" \
OTEL_EXPORTER_OTLP_ENDPOINT="$otel_endpoint" \
OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT="" \
"$repo_root/.venv/bin/python" "$repo_root/tools/dsh_native_acceptance.py" --scenario "$scenario"

if [[ "$backend" == "capture" ]]; then
  requests="$(wc -l < "$capture_file" | tr -d ' ')"
  if [[ "$requests" -lt 1 ]]; then
    echo "OBS-01 failed: no OTLP requests captured" >&2
    exit 1
  fi
  if rg -n '"contains_plaintext_prompt_marker": true' "$capture_file" >/dev/null; then
    echo "OBS-01 failed: captureContent=false marker found in captured payload" >&2
    exit 1
  fi
  "$repo_root/.venv/bin/python" - "$capture_file" <<'PY'
import json
import sys
from pathlib import Path

records = [json.loads(line) for line in Path(sys.argv[1]).read_text().splitlines()]
spans = [span for record in records for span in record.get("spans", [])]
if not spans:
    raise SystemExit("OBS-01 failed: OTLP trace request contained no decoded spans")
trace_ids = {span["trace_id"] for span in spans if span.get("trace_id")}
if not trace_ids:
    raise SystemExit("OBS-01 failed: decoded spans have no trace identity")
span_ids = {span["span_id"] for span in spans if span.get("span_id")}
if any(span.get("parent_span_id") and span["parent_span_id"] not in span_ids for span in spans):
    raise SystemExit("OBS-01 failed: span parent points to an absent span")
names = {str(span.get("name", "")).lower() for span in spans}
required = ("enter_ai_application", "invoke_agent", "react step", "chat ", "execute_tool")
if not all(any(token in name for name in names) for token in required):
    raise SystemExit(f"OBS-01 failed: incomplete DSH span tree in {sorted(names)}")
if not any("dsh.session.id" in span.get("attribute_keys", []) for span in spans):
    raise SystemExit("OBS-01 failed: dsh.session.id attribute is absent")
print(f"OBS-01 trace structure: {len(spans)} span(s), {len(trace_ids)} trace(s), names={sorted(names)}")
PY
  echo "OBS-01 passed: captured $requests OTLP request(s); content capture remained disabled"
elif [[ "$backend" == "jaeger" ]]; then
  echo "OBS-01 backend boot passed; inspect Jaeger at http://127.0.0.1:16686"
else
  echo "OBS-01 exporter isolation passed: DSH/Hub completed with OTLP endpoint unavailable"
fi
