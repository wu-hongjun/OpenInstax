#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-/}"
LIVE_ROOT=0
if [[ "${ROOT%/}" == "" || "${ROOT%/}" == "/" ]]; then
  LIVE_ROOT=1
fi

path_in_root() {
  local path="$1"
  printf '%s/%s' "${ROOT%/}" "${path#/}"
}

remove_path() {
  local path
  path="$(path_in_root "$1")"
  if [[ -e "${path}" || -L "${path}" ]]; then
    rm -rf "${path}"
    echo "removed ${path}"
  fi
}

disable_legacy_units() {
  local legacy_units=(
    instantbridge.service
    instantbridge-boot-splash.service
    instantbridge-usb0-rearm.service
    instantbridge-usb0-lost.service
  )

  if [[ "${LIVE_ROOT}" -eq 1 ]]; then
    systemctl disable --now "${legacy_units[@]}" >/dev/null 2>&1 || true
    systemctl reset-failed "${legacy_units[@]}" >/dev/null 2>&1 || true
    return
  fi

  systemctl --root="${ROOT%/}" disable "${legacy_units[@]}" >/dev/null 2>&1 || true
}

echo "Cleaning legacy InstantBridge install from ${ROOT}"
disable_legacy_units

remove_path /etc/systemd/system/instantbridge.service
remove_path /etc/systemd/system/instantbridge-boot-splash.service
remove_path /etc/systemd/system/instantbridge-usb0-rearm.service
remove_path /etc/systemd/system/instantbridge-usb0-lost.service
remove_path /etc/udev/rules.d/99-instantbridge-usb0.rules
remove_path /etc/dnsmasq.d/instax.conf
remove_path /etc/NetworkManager/conf.d/99-instantbridge-unmanaged-usb0.conf
remove_path /etc/InstantBridge
remove_path /opt/InstantBridge

if [[ "${LIVE_ROOT}" -eq 1 ]]; then
  systemctl daemon-reload
  udevadm control --reload-rules >/dev/null 2>&1 || true
fi

echo "Done. Shared USB gadget files are preserved for InstantLink Bridge ownership."
