#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
lock_file="$repo_root/infra/dsh/upstream.lock.json"
cache_root="${DSH_UPSTREAM_CACHE:-$repo_root/.cache/dsh-upstream}"
archive="$cache_root/archive.tar.gz"
source_dir="$cache_root/source"
staging_dir="$cache_root/source.staging"

read_lock() {
  node -e "const p=require(process.argv[1]); process.stdout.write(String(p[process.argv[2]]))" "$lock_file" "$1"
}

archive_url="$(read_lock archive_url)"
expected_sha="$(read_lock archive_sha256)"
commit="$(read_lock commit)"

mkdir -p "$cache_root"

if [[ ! -f "$archive" ]]; then
  tmp_archive="$archive.partial"
  rm -f "$tmp_archive"
  curl --fail --location --retry 3 --connect-timeout 20 --output "$tmp_archive" "$archive_url"
  mv "$tmp_archive" "$archive"
fi

if command -v sha256sum >/dev/null 2>&1; then
  actual_sha="$(sha256sum "$archive" | awk '{print $1}')"
else
  actual_sha="$(shasum -a 256 "$archive" | awk '{print $1}')"
fi

if [[ "$actual_sha" != "$expected_sha" ]]; then
  echo "DSH archive checksum mismatch: expected $expected_sha, got $actual_sha" >&2
  exit 1
fi

if [[ -f "$source_dir/.decision-hub-upstream-commit" ]] \
  && [[ "$(tr -d '\r\n' < "$source_dir/.decision-hub-upstream-commit")" == "$commit" ]]; then
  node "$repo_root/infra/dsh/verify-upstream.mjs" "$source_dir"
  echo "DSH upstream already verified at $source_dir"
  exit 0
fi

rm -rf "$staging_dir"
mkdir -p "$staging_dir"
tar -xzf "$archive" --strip-components=1 -C "$staging_dir"
printf '%s\n' "$commit" > "$staging_dir/.decision-hub-upstream-commit"
node "$repo_root/infra/dsh/verify-upstream.mjs" "$staging_dir"
rm -rf "$source_dir"
mv "$staging_dir" "$source_dir"
echo "DSH upstream fetched and verified at $source_dir"
