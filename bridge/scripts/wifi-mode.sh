#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-status}"
HOTSPOT_CONNECTION="${INSTANTLINK_BRIDGE_HOTSPOT_CONNECTION:-InstantLink Bridge-Hotspot}"
HOME_CONNECTION="${INSTANTLINK_BRIDGE_HOME_CONNECTION:-InstantLink Bridge-Home}"
CONFIG_FILE="${INSTANTLINK_BRIDGE_CONFIG:-/etc/InstantLinkBridge/config.toml}"
HOTSPOT_SSID_FILE="${INSTANTLINK_BRIDGE_HOTSPOT_SSID_FILE:-/etc/InstantLinkBridge/hotspot.ssid}"
FALLBACK_HOTSPOT_SSID="LinkBrdg-SETUP"
PSK_FILE="${INSTANTLINK_BRIDGE_HOTSPOT_PSK_FILE:-/etc/InstantLinkBridge/hotspot.psk}"
NM_READY_TIMEOUT_S="${INSTANTLINK_BRIDGE_NM_READY_TIMEOUT_S:-30}"
HOTSPOT_CHANNEL="${INSTANTLINK_BRIDGE_HOTSPOT_CHANNEL:-6}"

require_nmcli() {
  command -v nmcli >/dev/null || {
    echo "nmcli is required; install NetworkManager first" >&2
    exit 1
  }
}

wait_for_networkmanager() {
  local waited=0
  while (( waited < NM_READY_TIMEOUT_S )); do
    if nmcli -t -f RUNNING general 2>/dev/null | grep -qx "running"; then
      return
    fi
    sleep 1
    waited=$((waited + 1))
  done
  echo "NetworkManager is not running after ${NM_READY_TIMEOUT_S}s" >&2
  exit 1
}

run_root() {
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

hotspot_ssid() {
  if [[ -n "${INSTANTLINK_BRIDGE_HOTSPOT_SSID:-}" ]]; then
    printf '%s\n' "${INSTANTLINK_BRIDGE_HOTSPOT_SSID}"
    return
  fi
  if [[ -r "${HOTSPOT_SSID_FILE}" ]]; then
    local configured_file
    configured_file="$(sed -n '1p' "${HOTSPOT_SSID_FILE}")"
    if [[ -n "${configured_file}" ]]; then
      printf '%s\n' "${configured_file}"
      return
    fi
  fi
  if nmcli -t -f NAME connection show | grep -qxF "${HOTSPOT_CONNECTION}"; then
    local configured
    configured="$(nmcli -g 802-11-wireless.ssid connection show "${HOTSPOT_CONNECTION}")"
    if [[ -n "${configured}" ]]; then
      printf '%s\n' "${configured}"
      return
    fi
  fi
  default_hotspot_ssid
}

default_hotspot_ssid() {
  local machine_id
  local machine_id_path
  for machine_id_path in /etc/machine-id /var/lib/dbus/machine-id; do
    if [[ -r "${machine_id_path}" ]]; then
      machine_id="$(sed -n '1p' "${machine_id_path}" | tr '[:lower:]' '[:upper:]')"
      if [[ "${machine_id}" =~ ^[0-9A-F]{8} ]]; then
        printf 'LinkBrdg-%s\n' "${machine_id:0:8}"
        return
      fi
    fi
  done
  printf '%s\n' "${FALLBACK_HOTSPOT_SSID}"
}

hotspot_host() {
  if [[ -n "${INSTANTLINK_BRIDGE_HOTSPOT_HOST:-}" ]]; then
    printf '%s\n' "${INSTANTLINK_BRIDGE_HOTSPOT_HOST}"
    return
  fi
  if [[ -r "${CONFIG_FILE}" ]]; then
    local configured
    configured="$(awk '
      /^\[ftp\]$/ { in_ftp = 1; next }
      /^\[/ { in_ftp = 0 }
      in_ftp && /^hotspot_host = / {
        value = $0
        sub(/^hotspot_host = "/, "", value)
        sub(/"$/, "", value)
        print value
        exit
      }
    ' "${CONFIG_FILE}")"
    if [[ -n "${configured}" ]]; then
      printf '%s\n' "${configured}"
      return
    fi
  fi
  printf '%s\n' '192.168.8.1'
}

hotspot_psk() {
  if [[ -n "${INSTANTLINK_BRIDGE_HOTSPOT_PSK:-}" ]]; then
    if ! valid_hotspot_psk "${INSTANTLINK_BRIDGE_HOTSPOT_PSK}"; then
      echo "INSTANTLINK_BRIDGE_HOTSPOT_PSK must be exactly 8 digits" >&2
      exit 2
    fi
    printf '%s\n' "${INSTANTLINK_BRIDGE_HOTSPOT_PSK}"
    return
  fi
  local existing
  existing="$(read_hotspot_psk || true)"
  if [[ -n "${existing}" ]]; then
    ensure_hotspot_psk_permissions
    printf '%s\n' "${existing}"
    return
  fi
  run_root install -d -m 0750 "$(dirname "${PSK_FILE}")"
  local generated
  generated="$(generate_hotspot_psk)"
  write_hotspot_psk "${generated}"
  printf '%s\n' "${generated}"
}

valid_hotspot_psk() {
  [[ "$1" =~ ^[0-9]{8}$ ]]
}

read_hotspot_psk() {
  if [[ ! -r "${PSK_FILE}" ]]; then
    return 1
  fi
  local existing
  existing="$(sed -n '1p' "${PSK_FILE}")"
  if ! valid_hotspot_psk "${existing}"; then
    return 1
  fi
  printf '%s\n' "${existing}"
}

generate_hotspot_psk() {
  od -An -N4 -tu4 /dev/urandom | awk '{ printf "%08d\n", $1 % 100000000 }'
}

write_hotspot_psk() {
  printf '%s\n' "$1" | run_root tee "${PSK_FILE}" >/dev/null
  ensure_hotspot_psk_permissions
}

ensure_hotspot_psk_permissions() {
  if getent group ib >/dev/null 2>&1; then
    run_root chgrp ib "${PSK_FILE}"
  fi
  run_root chmod 0640 "${PSK_FILE}"
}

configure_hotspot() {
  local host
  local psk
  local ssid
  host="$(hotspot_host)"
  ssid="$(hotspot_ssid)"
  psk="$(hotspot_psk)"
  run_root nmcli radio wifi on
  if ! nmcli -t -f NAME connection show | grep -qxF "${HOTSPOT_CONNECTION}"; then
    run_root nmcli connection add type wifi ifname wlan0 con-name "${HOTSPOT_CONNECTION}" \
      ssid "${ssid}"
  fi
  run_root nmcli connection modify "${HOTSPOT_CONNECTION}" \
    connection.autoconnect no \
    802-11-wireless.mode ap \
    802-11-wireless.band bg \
    802-11-wireless.channel "${HOTSPOT_CHANNEL}" \
    802-11-wireless.ssid "${ssid}" \
    wifi-sec.key-mgmt wpa-psk \
    wifi-sec.proto rsn \
    wifi-sec.pairwise ccmp \
    wifi-sec.group ccmp \
    wifi-sec.psk "${psk}" \
    ipv4.method shared \
    ipv4.addresses "${host}/24" \
    ipv4.never-default yes \
    ipv6.method disabled
  run_root nmcli connection up "${HOTSPOT_CONNECTION}"
  echo "Hotspot active: SSID=${ssid} FTP=${host}"
}

configure_home() {
  local ssid="${2:-}"
  local password="${3:-}"
  if [[ -z "${ssid}" || -z "${password}" ]]; then
    echo "Usage: scripts/wifi-mode.sh home <ssid> <password>" >&2
    exit 2
  fi
  run_root nmcli radio wifi on
  if nmcli -t -f NAME connection show | grep -qxF "${HOME_CONNECTION}"; then
    run_root nmcli connection modify "${HOME_CONNECTION}" \
      802-11-wireless.ssid "${ssid}" \
      wifi-sec.key-mgmt wpa-psk \
      wifi-sec.psk "${password}" \
      ipv4.method auto \
      ipv4.never-default no \
      ipv6.method disabled
  else
    run_root nmcli connection add type wifi ifname wlan0 con-name "${HOME_CONNECTION}" ssid "${ssid}" \
      wifi-sec.key-mgmt wpa-psk wifi-sec.psk "${password}" ipv4.method auto ipv6.method disabled
  fi
  run_root nmcli connection down "${HOTSPOT_CONNECTION}" >/dev/null 2>&1 || true
  run_root nmcli connection up "${HOME_CONNECTION}"
  echo "Home Wi-Fi active: SSID=${ssid}"
}

configure_home_saved() {
  local connection="${2:-}"
  if [[ -z "${connection}" ]]; then
    if nmcli -t -f NAME connection show | grep -qxF "${HOME_CONNECTION}"; then
      connection="${HOME_CONNECTION}"
    else
      connection="$(
        nmcli -t -f NAME,TYPE,AUTOCONNECT connection show |
          awk -F: -v hotspot="${HOTSPOT_CONNECTION}" \
            '$2 == "802-11-wireless" && $1 != hotspot && $3 == "yes" { print $1; exit }'
      )"
    fi
  fi
  if [[ -z "${connection}" ]]; then
    echo "No saved home Wi-Fi connection found" >&2
    exit 1
  fi
  run_root nmcli radio wifi on
  run_root nmcli connection down "${HOTSPOT_CONNECTION}" >/dev/null 2>&1 || true
  run_root nmcli connection up "${connection}"
  echo "Home Wi-Fi active: connection=${connection}"
}

status() {
  nmcli -f NAME,TYPE,DEVICE,AUTOCONNECT connection show
  ip -4 -o addr show dev wlan0 2>/dev/null || true
  if nmcli -t -f NAME connection show | grep -qxF "${HOTSPOT_CONNECTION}"; then
    echo "Configured hotspot SSID: $(hotspot_ssid)"
    local psk
    psk="$(read_hotspot_psk || true)"
    echo "Configured hotspot PIN: ${psk:-not set}"
  fi
}

require_nmcli
wait_for_networkmanager
case "${MODE}" in
  hotspot)
    configure_hotspot
    ;;
  home)
    configure_home "$@"
    ;;
  home-saved)
    configure_home_saved "$@"
    ;;
  off)
    run_root nmcli connection down "${HOTSPOT_CONNECTION}" >/dev/null 2>&1 || true
    run_root nmcli radio wifi off
    echo "Wi-Fi radio disabled"
    ;;
  status)
    status
    ;;
  *)
    echo "Usage: scripts/wifi-mode.sh {status|hotspot|home <ssid> <password>|home-saved [connection]|off}" >&2
    exit 2
    ;;
esac
