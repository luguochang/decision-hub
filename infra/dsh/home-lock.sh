#!/usr/bin/env bash

# Cross-process ownership guard for one DSH_HOME. This file is sourced by
# run-web.sh so the owner PID survives its final exec into the official DSH CLI.

dsh_process_start_signature() {
  local pid="$1"
  local signature
  # procps (Linux/WSL) exposes the full start time through `-o lstart=`.
  # MSYS2's `ps` does not implement `-o`; its stable WINPID + STIME pair is
  # sufficient to distinguish a live owner from a reused shell PID.
  signature="$(ps -p "$pid" -o lstart= 2>/dev/null | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  if [[ -n "$signature" ]]; then
    printf '%s\n' "$signature"
    return 0
  fi
  ps -p "$pid" 2>/dev/null | awk 'NR == 2 { print $4 "|" $7 }'
}

dsh_windows_signature_is_live() {
  local signature="$1"
  local winpid
  local started
  [[ "$signature" == *"|"* ]] || return 1
  winpid="${signature%%|*}"
  started="${signature#*|}"
  [[ "$winpid" =~ ^[1-9][0-9]*$ && -n "$started" ]] || return 1
  # Separate MSYS2 shells cannot signal each other's synthetic PID, but ps -W
  # exposes the shared Windows PID and start time used in our fallback signature.
  ps -W 2>/dev/null | awk -v pid="$winpid" -v started="$started" \
    'NR > 1 && $4 == pid && $7 == started { found = 1 } END { exit !found }'
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
    elif dsh_windows_signature_is_live "$owner_start"; then
      live_start="$owner_start"
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
