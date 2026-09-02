#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cache_root="${DSH_UPSTREAM_CACHE:-$repo_root/.cache/dsh-upstream}"
source_dir="$cache_root/source"

if [[ "${DSH_ACCEPT_DOWNLOAD:-0}" == "1" ]]; then
  "$repo_root/infra/dsh/fetch-upstream.sh"
fi

if [[ ! -d "$source_dir" ]]; then
  echo "DSH source cache is absent. Run ./infra/dsh/fetch-upstream.sh or set DSH_ACCEPT_DOWNLOAD=1." >&2
  exit 1
fi

node "$repo_root/infra/dsh/verify-upstream.mjs" "$source_dir"

if [[ ! -f "$source_dir/apps/web/dist/index.html" ]]; then
  echo "Official Web build is absent; run ./infra/dsh/build-upstream.sh." >&2
  exit 1
fi

node -e "const fs=require('node:fs'); const p=process.argv[1]; const s=fs.statSync(p); if(s.size<256) throw new Error('DSH Web index is unexpectedly small')" "$source_dir/apps/web/dist/index.html"
if [[ "${DSH_ACCEPT_WEB:-1}" == "1" ]]; then
  "$repo_root/infra/dsh/web-smoke.sh"
fi
echo "DSH NATIVE-00 source, build and authenticated Web acceptance passed"
