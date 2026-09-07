#!/usr/bin/env bash

# Cross-process ownership guard for one DSH_HOME. This file is sourced by
# run-web.sh so the owner PID survives its final exec into the official DSH CLI.

dsh_process_start_signature() {
  local pid="$1"
  ps -p "$pid" -o lstart= 2>/dev/null | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}

dsh_read_lock_value() {
  local metadata="$1"
  local key="$2"
  sed -nE "s/^${key}=(.*)$/\\1/p" "$metadata" 2>/dev/null | head -n 1
}

dsh_acquire_home_lock() {
  local requested_home="$1"
  local canonical_home
  local lock_dir
  local metadata
  local self_start
  local owner_pid
  local owner_start
  local owner_home
  local live_start
  local quarantine
  local metadata_tmp
  local wait_attempt

  if [[ ! -d "$requested_home" ]]; then
    echo "DSH home lock requires an existing directory: $requested_home" >&2
    return 2
  fi
  canonical_home="$(cd "$requested_home" && pwd -P)"
  lock_dir="$canonical_home/.decision-hub-dsh-web.lock"
  metadata="$lock_dir/owner"
  self_start="$(dsh_process_start_signature "$$")"
  if [[ -z "$self_start" ]]; then
    echo "Unable to determine DSH Web owner process identity" >&2
    return 1
  fi

  while ! mkdir "$lock_dir" 2>/dev/null; do
    # A winning process writes metadata immediately after mkdir. Give that
    # atomic acquisition a bounded moment before classifying an empty lock.
    wait_attempt=0
    while [[ ! -f "$metadata" && $wait_attempt -lt 20 ]]; do
      sleep 0.05
      wait_attempt=$((wait_attempt + 1))
    done

    owner_pid="$(dsh_read_lock_value "$metadata" pid)"
    owner_start="$(dsh_read_lock_value "$metadata" started)"
    owner_home="$(dsh_read_lock_value "$metadata" home)"
    live_start=""
    if [[ "$owner_pid" =~ ^[1-9][0-9]*$ ]] && kill -0 "$owner_pid" 2>/dev/null; then
      live_start="$(dsh_process_start_signature "$owner_pid")"
    fi
    if [[ -n "$live_start" && "$live_start" == "$owner_start" && "$owner_home" == "$canonical_home" ]]; then
      echo "DSH Web already owns DSH_WEB_HOME $canonical_home (pid $owner_pid); use a different DSH_WEB_HOME or stop the existing instance" >&2
      return 73
    fi

    quarantine="${lock_dir}.stale.$$"
    if ! mv "$lock_dir" "$quarantine" 2>/dev/null; then
      continue
    fi
    rm -f "$quarantine/owner"
    if ! rmdir "$quarantine" 2>/dev/null; then
      echo "Refusing to remove unexpected files from stale DSH Web lock: $quarantine" >&2
      return 1
    fi
  done

  metadata_tmp="$lock_dir/owner.$$"
  umask 077
  {
    printf 'pid=%s\n' "$$"
    printf 'started=%s\n' "$self_start"
    printf 'home=%s\n' "$canonical_home"
  } > "$metadata_tmp"
  mv "$metadata_tmp" "$metadata"
  export DECISION_HUB_DSH_HOME_LOCK="$lock_dir"
}
