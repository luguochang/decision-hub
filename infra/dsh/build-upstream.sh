#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cache_root="${DSH_UPSTREAM_CACHE:-$repo_root/.cache/dsh-upstream}"
source_dir="$cache_root/source"

"$repo_root/infra/dsh/fetch-upstream.sh"
node "$repo_root/infra/dsh/verify-upstream.mjs" "$source_dir"

expected_pnpm="$(node -e "const p=require(process.argv[1]); process.stdout.write(p.pnpm)" "$repo_root/infra/dsh/upstream.lock.json")"
if command -v corepack >/dev/null 2>&1; then
  pnpm_runner=(corepack "pnpm@$expected_pnpm")
else
  pnpm_runner=(npx --yes "pnpm@$expected_pnpm")
fi

cd "$source_dir"
LEFTHOOK=0 "${pnpm_runner[@]}" install --frozen-lockfile
"${pnpm_runner[@]}" run build:official
node "$repo_root/infra/dsh/verify-upstream.mjs" "$source_dir"
echo "DSH official source closure built at $source_dir"
