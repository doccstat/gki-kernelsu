#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 CONFIG VARIANT" >&2
  exit 2
fi

config_file=$1
variant=$2
[[ -s "$config_file" ]] || { echo "kernel config is empty: $config_file" >&2; exit 1; }

config_value() {
  local key="CONFIG_$1"
  local line
  line=$(grep -E "^${key}=" "$config_file" | tail -n 1 || true)
  if [[ -n "$line" ]]; then
    printf '%s\n' "${line#*=}"
  elif grep -q "^# ${key} is not set$" "$config_file"; then
    printf 'n\n'
  else
    printf 'missing\n'
  fi
}

require_value() {
  local key=$1 expected=$2 actual
  actual=$(config_value "$key")
  [[ "$actual" == "$expected" ]] || {
    echo "invalid kernel config: CONFIG_${key}=${actual}, expected ${expected}" >&2
    exit 1
  }
}

require_disabled() {
  local key=$1 actual
  actual=$(config_value "$key")
  [[ "$actual" == n || "$actual" == missing ]] || {
    echo "invalid kernel config: CONFIG_${key} must be disabled, got ${actual}" >&2
    exit 1
  }
}

require_value MODULES y
require_value MODVERSIONS y
require_value MODULE_SIG y
require_value MODULE_SIG_ALL y
require_disabled MODULE_SIG_FORCE
require_value MODULE_SIG_PROTECT_LIST '""'
require_disabled MODULE_SIG_PROTECT
require_value KSU y

case "$variant" in
  resukisu-susfs)
    require_value KSU_SUSFS y
    require_disabled KPM
    require_disabled KSU_TRACEPOINT_HOOK
    ;;
  sukisu-kpm)
    require_value KPM y
    require_disabled KSU_SUSFS
    ;;
  *)
    echo "unknown kernel variant: $variant" >&2
    exit 2
    ;;
esac

echo "validated final kernel config for $variant"
