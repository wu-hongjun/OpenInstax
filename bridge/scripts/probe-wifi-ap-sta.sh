#!/usr/bin/env bash
set -u

HOTSPOT_CONNECTION="${INSTANTLINK_BRIDGE_HOTSPOT_CONNECTION:-InstantLink Bridge-Hotspot}"
HOME_CONNECTION="${INSTANTLINK_BRIDGE_HOME_CONNECTION:-}"
AP0_CONNECTION="${INSTANTLINK_BRIDGE_AP0_CONNECTION:-InstantLink Bridge-Hotspot-ap0}"
AP0_IFACE="${INSTANTLINK_BRIDGE_AP0_IFACE:-ib-ap0}"
HOLD_S="${INSTANTLINK_BRIDGE_PROBE_HOLD_S:-8}"
LOG_PATH="${INSTANTLINK_BRIDGE_PROBE_LOG:-/tmp/instantlink-bridge-ap-sta-probe.log}"
WIFI_MODE_HELPER="${INSTANTLINK_BRIDGE_WIFI_MODE_HELPER:-/usr/local/sbin/instantlink-bridge-wifi-mode}"
CONFIG_PATH="${INSTANTLINK_BRIDGE_CONFIG:-/etc/InstantLinkBridge/config.toml}"
SSID_FILE="${INSTANTLINK_BRIDGE_HOTSPOT_SSID_FILE:-/etc/InstantLinkBridge/hotspot.ssid}"
PSK_FILE="${INSTANTLINK_BRIDGE_HOTSPOT_PSK_FILE:-/etc/InstantLinkBridge/hotspot.psk}"

mkdir -p "$(dirname "${LOG_PATH}")"
exec > >(tee "${LOG_PATH}") 2>&1

log() {
  printf '\n[%s] %s\n' "$(date -Is)" "$*"
}

run() {
  local rc
  log "RUN $*"
  "$@"
  rc=$?
  log "RC ${rc}: $*"
  return 0
}

run_ap0_modify() {
  local host
  local psk
  local rc
  local ssid
  host="$(hotspot_host)"
  psk="$(hotspot_psk)"
  ssid="$(hotspot_ssid)"
  log "RUN nmcli connection modify ${AP0_CONNECTION} ... wifi-sec.psk ******** ipv4.addresses ${host}/24"
  nmcli connection modify "${AP0_CONNECTION}" \
    connection.autoconnect no \
    802-11-wireless.mode ap \
    802-11-wireless.band bg \
    802-11-wireless.channel 6 \
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
  rc=$?
  log "RC ${rc}: nmcli connection modify ${AP0_CONNECTION}"
  return 0
}

active_wifi_connection() {
  nmcli -t -f NAME,TYPE,DEVICE,ACTIVE connection show |
    awk -F: '$2 == "802-11-wireless" && $3 == "wlan0" && $4 == "yes" { print $1; exit }'
}

hotspot_ssid() {
  if [[ -f "${SSID_FILE}" ]]; then
    local configured
    configured="$(head -n 1 "${SSID_FILE}")"
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
  printf '%s\n' 'LinkBrdg-SETUP'
}

hotspot_psk() {
  if [[ -f "${PSK_FILE}" ]]; then
    head -n 1 "${PSK_FILE}"
    return
  fi
  printf '%s\n' '12345678'
}

hotspot_host() {
  if [[ -f "${CONFIG_PATH}" ]]; then
    awk '
      /^\[ftp\]/ { in_ftp=1; next }
      /^\[/ && in_ftp { in_ftp=0 }
      in_ftp && /^hotspot_host = / {
        value=$0
        sub(/^hotspot_host = "/, "", value)
        sub(/"$/, "", value)
        print value
        found=1
        exit
      }
      END { if (!found) exit 1 }
    ' "${CONFIG_PATH}" 2>/dev/null && return
  fi
  printf '%s\n' '192.168.8.1'
}

snapshot() {
  log "SNAPSHOT $*"
  nmcli -f NAME,TYPE,DEVICE,AUTOCONNECT,ACTIVE connection show || true
  nmcli -f DEVICE,TYPE,STATE,CONNECTION device status || true
  ip -br addr show wlan0 2>/dev/null || true
  ip -br addr show "${AP0_IFACE}" 2>/dev/null || true
  ip route get 100.100.100.100 2>/dev/null || true
  if command -v iw >/dev/null 2>&1; then
    iw dev || true
    iw list 2>/dev/null | sed -n '/valid interface combinations/,+22p' || true
  else
    echo "iw not installed; skipping driver capability dump"
  fi
}

restore_home() {
  log "RESTORE start"
  nmcli connection down "${AP0_CONNECTION}" >/dev/null 2>&1 || true
  nmcli connection delete "${AP0_CONNECTION}" >/dev/null 2>&1 || true
  if command -v iw >/dev/null 2>&1; then
    iw dev "${AP0_IFACE}" del >/dev/null 2>&1 || true
  fi
  nmcli connection down "${HOTSPOT_CONNECTION}" >/dev/null 2>&1 || true
  if [[ -n "${HOME_CONNECTION}" ]]; then
    nmcli radio wifi on >/dev/null 2>&1 || true
    nmcli connection up "${HOME_CONNECTION}" >/dev/null 2>&1 || true
  fi
  snapshot "after restore"
}

trap restore_home EXIT

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root, for example: sudo $0" >&2
  exit 2
fi

if ! command -v nmcli >/dev/null 2>&1; then
  echo "nmcli is required" >&2
  exit 2
fi

if [[ -z "${HOME_CONNECTION}" ]]; then
  HOME_CONNECTION="$(active_wifi_connection)"
fi

log "InstantLink Bridge AP+STA probe"
log "home=${HOME_CONNECTION:-none} hotspot=${HOTSPOT_CONNECTION} ap0=${AP0_IFACE}"
snapshot "before"

log "Attempt 1: bring the normal hotspot profile up while home Wi-Fi is active"
if [[ -x "${WIFI_MODE_HELPER}" ]]; then
  run "${WIFI_MODE_HELPER}" hotspot
else
  run nmcli connection up "${HOTSPOT_CONNECTION}"
fi
sleep "${HOLD_S}"
snapshot "after normal hotspot up"

log "Attempt 2: bring home Wi-Fi back up while the normal hotspot profile is active"
if [[ -n "${HOME_CONNECTION}" ]]; then
  run nmcli connection up "${HOME_CONNECTION}"
  sleep "${HOLD_S}"
  snapshot "after home up over normal hotspot"
else
  log "SKIP no active home Wi-Fi connection was detected at probe start"
fi

log "Attempt 3: create a virtual AP interface and run hotspot there while wlan0 stays home Wi-Fi"
if ! command -v iw >/dev/null 2>&1; then
  log "SKIP iw is not installed"
else
  if [[ -n "${HOME_CONNECTION}" ]]; then
    run nmcli connection up "${HOME_CONNECTION}"
    sleep 3
  fi
  run iw dev "${AP0_IFACE}" del
  run iw dev wlan0 interface add "${AP0_IFACE}" type __ap
  run nmcli connection add type wifi ifname "${AP0_IFACE}" con-name "${AP0_CONNECTION}" \
    ssid "$(hotspot_ssid)"
  run_ap0_modify
  run nmcli connection up "${AP0_CONNECTION}"
  sleep "${HOLD_S}"
  snapshot "after virtual ap0 hotspot up"
fi

log "Probe complete; restore will run through EXIT trap"
