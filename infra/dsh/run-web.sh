#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cache_root="${DSH_UPSTREAM_CACHE:-$repo_root/.cache/dsh-upstream}"
source_dir="$cache_root/source"
web_home="${DSH_WEB_HOME:-$repo_root/data/dsh-web}"
host="127.0.0.1"
port="3080"
patches=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      host="$2"
      shift 2
      ;;
    --port)
      port="$2"
      shift 2
      ;;
    --patch)
      patch_path="$2"
      if [[ "$patch_path" != /* ]]; then
        patch_path="$repo_root/$patch_path"
      fi
      if [[ ! -f "$patch_path" ]]; then
        echo "DSH Web patch does not exist: $patch_path" >&2
        exit 2
      fi
      patches+=("$patch_path")
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ "$host" != "127.0.0.1" && "$host" != "localhost" && "$host" != "::1" ]]; then
  echo "DSH Web must remain loopback-only; refusing host $host" >&2
  exit 2
fi

if [[ ! -f "$source_dir/apps/web/dist/index.html" || ! -f "$source_dir/apps/cli/lib/bin.js" ]]; then
  "$repo_root/infra/dsh/build-upstream.sh"
else
  node "$repo_root/infra/dsh/verify-upstream.mjs" "$source_dir" >/dev/null
fi
mkdir -p "$web_home"
source "$repo_root/infra/dsh/home-lock.sh"
dsh_acquire_home_lock "$web_home"

expected_pnpm="$(node -e "const p=require(process.argv[1]); process.stdout.write(p.pnpm)" "$repo_root/infra/dsh/upstream.lock.json")"
if command -v corepack >/dev/null 2>&1; then
  pnpm_runner=(corepack "pnpm@$expected_pnpm")
else
  pnpm_runner=(npx --yes "pnpm@$expected_pnpm")
fi
dsh_cli=(node "$source_dir/apps/cli/lib/bin.js")

export DSH_HOME="$web_home"
plugin_dir="$repo_root/extensions/dsh/decision-hub"
preset_root="$repo_root/infra/dsh/presets"

# Optional official DSH observability bundle. It is deliberately opt-in so
# the product launcher and existing replay/live profiles keep their current
# dependency closure. The version is read from the repository lock and the
# package is installed through DSH's own profile plugin command; no upstream
# source or business module imports the plugin.
if [[ "${DSH_OBSERVABILITY_ENABLED:-0}" == "1" ]]; then
  observability_lock="$repo_root/infra/dsh/observability/plugin.lock.json"
  if [[ ! -f "$observability_lock" ]]; then
    echo "DSH observability lock is missing: $observability_lock" >&2
    exit 1
  fi
  observability_package="$(node -e "const p=require(process.argv[1]); if(p.package !== '@loongsuite/dsh-plugin' || p.version !== '0.1.2') process.exit(1); process.stdout.write(p.package + '@' + p.version)" "$observability_lock")" || {
    echo "DSH observability lock does not contain the approved package/version" >&2
    exit 1
  }
  "${dsh_cli[@]}" plugin --profile web add --save-exact "$observability_package"
  node - "$web_home/profiles/web/package.json" "$observability_package" <<'NODE'
const fs = require('node:fs')
const [manifestPath, packageSpec] = process.argv.slice(2)
const separator = packageSpec.lastIndexOf('@')
const name = packageSpec.slice(0, separator)
const version = packageSpec.slice(separator + 1)
const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'))
if (manifest.dependencies?.[name] !== version) {
  throw new Error(`observability package is not exact-pinned: ${name}=${manifest.dependencies?.[name]}`)
}
NODE
  observability_integrity="$(node -e "const p=require(process.argv[1]); process.stdout.write(p.integrity)" "$observability_lock")"
  node - "$web_home/profiles/web/pnpm-lock.yaml" "$observability_integrity" <<'NODE'
const fs = require('node:fs')
const [lockPath, integrity] = process.argv.slice(2)
const lock = fs.readFileSync(lockPath, 'utf8')
const match = lock.match(/'@loongsuite\/dsh-plugin@0\.1\.2':\n(?:  .*\n)*?    resolution: \{integrity: ([^}]+)\}/)
if (match?.[1] !== integrity) {
  throw new Error('observability pnpm lock integrity does not match plugin.lock.json')
}
NODE
fi

# Build the plugin before it is installed into the profile. The upstream
# client-modules host serves the packaged `./client` export and fails loudly
# when that artifact is absent or stale.
"${pnpm_runner[@]}" --dir "$plugin_dir" build

# Use the official profile-plugin command. It updates only the DSH profile
# manifest under DSH_HOME; the pinned upstream source tree remains untouched.
"${dsh_cli[@]}" plugin --profile web add "$plugin_dir"

# The upstream replay adapter is test-support code, not part of the shipped
# Web dependency closure. An explicit replay run may install it into the
# temporary profile so an official overlay can load it by its package name.
if [[ -n "${DSH_REPLAY_PLUGIN:-}" ]]; then
  if [[ ! -f "$DSH_REPLAY_PLUGIN/package.json" ]]; then
    echo "DSH_REPLAY_PLUGIN must point to an upstream package directory" >&2
    exit 1
  fi
  "${pnpm_runner[@]}" --dir "$web_home/profiles/web" add "$DSH_REPLAY_PLUGIN"
fi

# Install the repository-controlled research composition into the official
# agent-presets user root. This copies only the two preset files and never
# modifies the pinned upstream source or an existing unrelated preset.
research_preset_source="$preset_root/decision-research"
research_preset_target="$web_home/.agent-presets/decision-research"
if [[ ! -f "$research_preset_source/agent.cordis.yml" || ! -f "$research_preset_source/preset.yml" ]]; then
  echo "Decision Hub research preset is incomplete" >&2
  exit 1
fi
mkdir -p "$research_preset_target"
cp "$research_preset_source/agent.cordis.yml" "$research_preset_target/agent.cordis.yml"
cp "$research_preset_source/preset.yml" "$research_preset_target/preset.yml"
chmod 700 "$research_preset_target"
chmod 600 "$research_preset_target/agent.cordis.yml" "$research_preset_target/preset.yml"

if [[ -z "${DECISION_HUB_DSH_SOURCE_COMMIT:-}" ]]; then
  export DECISION_HUB_DSH_SOURCE_COMMIT="$(node -e "const p=require(process.argv[1]); process.stdout.write(p.commit)" "$repo_root/infra/dsh/upstream.lock.json")"
else
  export DECISION_HUB_DSH_SOURCE_COMMIT
fi
if [[ -z "${DECISION_HUB_DSH_SOURCE_VERSION:-}" ]]; then
  export DECISION_HUB_DSH_SOURCE_VERSION="$(node -e "const p=require(process.argv[1]); process.stdout.write(p.source_version)" "$repo_root/infra/dsh/upstream.lock.json")"
else
  export DECISION_HUB_DSH_SOURCE_VERSION
fi
if [[ ! -f "$plugin_dir/src/generated-build-identity.ts" ]]; then
  echo "Decision Hub plugin build identity was not generated" >&2
  exit 1
fi
if [[ -z "${DECISION_HUB_DSH_PLUGIN_BUILD_HASH:-}" ]]; then
  export DECISION_HUB_DSH_PLUGIN_BUILD_HASH="$(sed -nE 's/^export const PLUGIN_BUILD_HASH = "([a-f0-9]{64})" as const;$/\1/p' "$plugin_dir/src/generated-build-identity.ts")"
else
  export DECISION_HUB_DSH_PLUGIN_BUILD_HASH
fi
if [[ ! "$DECISION_HUB_DSH_PLUGIN_BUILD_HASH" =~ ^[a-f0-9]{64}$ ]]; then
  echo "Decision Hub plugin build identity is missing or invalid" >&2
  exit 1
fi
export DECISION_HUB_DSH_HOST_KEY="${DECISION_HUB_DSH_HOST_KEY:-decision-hub-local-host-key}"
export DECISION_HUB_DSH_CALLBACK_KEY="${DECISION_HUB_DSH_CALLBACK_KEY:-${DECISION_HUB_DSH_CALLBACK_SECRET:-decision-hub-local-callback-key}}"
if [[ -z "${DECISION_HUB_DSH_RUNTIME_MODE:-}" ]]; then
  if [[ -n "${DSH_REPLAY_PLUGIN:-}" ]]; then
    export DECISION_HUB_DSH_RUNTIME_MODE="replay"
  else
    export DECISION_HUB_DSH_RUNTIME_MODE="live"
  fi
fi
case "$DECISION_HUB_DSH_RUNTIME_MODE" in
  live|replay) ;;
  *)
    echo "DECISION_HUB_DSH_RUNTIME_MODE must be live or replay" >&2
    exit 2
    ;;
esac
printf 'dsh decision-hub runtime: %s (%s)\n' "$DECISION_HUB_DSH_RUNTIME_MODE" \
  "$([[ "$DECISION_HUB_DSH_RUNTIME_MODE" == replay ]] && printf 'read-only acceptance' || printf 'interactive provider')" >&2
args=(--profile web)
if [[ ${#patches[@]} -gt 0 ]]; then
  for patch in "${patches[@]}"; do
    args+=(--patch "$patch")
  done
fi
args+=(--host "$host" --port "$port" --no-open)

cd "$source_dir"
exec "${dsh_cli[@]}" "${args[@]}"
