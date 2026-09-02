#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
smoke_root="$(mktemp -d /tmp/decision-hub-dsh-smoke.XXXXXX)"
log_file="$smoke_root/dsh-web.log"
cookie_file="$smoke_root/cookies.txt"
html_file="$smoke_root/index.html"
web_pid=""

cleanup() {
  if [[ -n "$web_pid" ]]; then
    kill "$web_pid" >/dev/null 2>&1 || true
    wait "$web_pid" >/dev/null 2>&1 || true
  fi
  case "$smoke_root" in
    /tmp/decision-hub-dsh-smoke.*) rm -rf -- "$smoke_root" ;;
  esac
}
trap cleanup EXIT INT TERM

DSH_WEB_HOME="$smoke_root/home" "$repo_root/infra/dsh/run-web.sh" --port 0 >"$log_file" 2>&1 &
web_pid="$!"

web_url=""
for _ in $(seq 1 120); do
  if ! kill -0 "$web_pid" >/dev/null 2>&1; then
    sed -E 's/(token=)[^[:space:]]+/\1[redacted]/g' "$log_file" >&2
    echo "DSH Web exited before readiness" >&2
    exit 1
  fi
  web_url="$(sed -nE 's#^dsh web: (http://127\.0\.0\.1:[0-9]+/\?token=[^[:space:]]+).*#\1#p' "$log_file" | tail -1)"
  [[ -n "$web_url" ]] && break
  sleep 0.1
done

if [[ -z "$web_url" ]]; then
  sed -E 's/(token=)[^[:space:]]+/\1[redacted]/g' "$log_file" >&2
  echo "DSH Web did not publish an authenticated URL" >&2
  exit 1
fi

curl --fail --silent --show-error --max-time 10 --location \
  --cookie-jar "$cookie_file" "$web_url" --output "$html_file"

node -e "
  const fs = require('node:fs')
  const html = fs.readFileSync(process.argv[1], 'utf8')
  if (html.length < 4096) throw new Error('authenticated DSH Web HTML is unexpectedly small')
  for (const marker of ['__ModuleLoader__', '__DSH_BOOT__']) {
    if (!html.includes(marker)) throw new Error('missing DSH Web boot marker: ' + marker)
  }
" "$html_file"

authority="$(printf '%s' "$web_url" | sed -E 's#^http://([^/]+)/.*#\1#')"
echo "DSH Web authenticated boot smoke passed at http://$authority/"
