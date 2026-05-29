#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-/}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIVE_ROOT=0
if [[ "${ROOT%/}" == "" || "${ROOT%/}" == "/" ]]; then
  LIVE_ROOT=1
fi

install_file() {
  local source="$1"
  local target="$2"
  install -D -m 0644 "${REPO_DIR}/${source}" "${ROOT%/}/${target#/}"
}

install_private_file() {
  local source="$1"
  local target="$2"
  install -D -m 0600 "${REPO_DIR}/${source}" "${ROOT%/}/${target#/}"
}

install_executable() {
  local source="$1"
  local target="$2"
  install -D -m 0755 "${REPO_DIR}/${source}" "${ROOT%/}/${target#/}"
}

install_sudoers_file() {
  local source="$1"
  local target="$2"
  install -D -m 0440 "${REPO_DIR}/${source}" "${ROOT%/}/${target#/}"
}

target_runtime_gid() {
  if [[ "${LIVE_ROOT}" -eq 1 ]]; then
    getent group ib | cut -d: -f3
  else
    awk -F: '$1 == "ib" { print $3 }' "${ROOT%/}/etc/group"
  fi
}

target_runtime_uid() {
  if [[ "${LIVE_ROOT}" -eq 1 ]]; then
    id -u ib
  else
    awk -F: '$1 == "ib" { print $3 }' "${ROOT%/}/etc/passwd"
  fi
}

chgrp_runtime() {
  local gid
  gid="$(target_runtime_gid || true)"
  if [[ -z "${gid}" ]]; then
    echo "ERROR: target root lacks ib group; provision runtime identity first" >&2
    exit 1
  fi
  chgrp "${gid}" "$@"
}

chown_runtime() {
  local gid
  local uid
  uid="$(target_runtime_uid || true)"
  gid="$(target_runtime_gid || true)"
  if [[ -z "${uid}" || -z "${gid}" ]]; then
    echo "ERROR: target root lacks ib user/group; provision runtime identity first" >&2
    exit 1
  fi
  chown "${uid}:${gid}" "$@"
}

ensure_runtime_identity() {
  if [[ "${LIVE_ROOT}" -ne 1 ]]; then
    echo "Offline root detected; expecting ib user/group in ${ROOT%/}/etc/passwd" >&2
    return
  fi
  if ! getent group ib >/dev/null 2>&1; then
    groupadd --system ib
  fi
  if ! id -u ib >/dev/null 2>&1; then
    useradd --system --gid ib --home-dir /var/lib/InstantLinkBridge \
      --no-create-home --shell /usr/sbin/nologin ib
  fi
  for group in bluetooth gpio spi i2c plugdev video; do
    if getent group "${group}" >/dev/null 2>&1; then
      usermod -aG "${group}" ib
    fi
  done
}

ensure_state_dirs() {
  install -d -m 2770 "${ROOT%/}/var/lib/InstantLinkBridge"
  install -d -m 2770 "${ROOT%/}/var/lib/InstantLinkBridge/incoming"
  install -d -m 0700 "${ROOT%/}/var/lib/InstantLinkBridge/management"
  install -d -m 0700 "${ROOT%/}/var/lib/InstantLinkBridge/management/clients"
  chown_runtime \
    "${ROOT%/}/var/lib/InstantLinkBridge" \
    "${ROOT%/}/var/lib/InstantLinkBridge/incoming" \
    "${ROOT%/}/var/lib/InstantLinkBridge/management" \
    "${ROOT%/}/var/lib/InstantLinkBridge/management/clients"
}

tighten_netplan_permissions() {
  local dir
  for dir in "${ROOT%/}/etc/netplan" "${ROOT%/}/lib/netplan" "${ROOT%/}/usr/lib/netplan"; do
    [[ -d "${dir}" ]] || continue
    find "${dir}" -maxdepth 1 -type f -name '*.yaml' -exec chmod 0600 {} +
  done
}

enable_service() {
  if [[ "${LIVE_ROOT}" -eq 1 ]]; then
    systemctl daemon-reload
    systemctl enable \
      instantlink-bridge.service \
      instantlink-bridge-boot-splash.service \
      instantlink-bridge-manager.service
    return
  fi
  systemctl --root="${ROOT%/}" enable \
    instantlink-bridge.service \
    instantlink-bridge-boot-splash.service \
    instantlink-bridge-manager.service
}

disable_legacy_instantbridge_services() {
  local legacy_units=(
    instantbridge.service
    instantbridge-boot-splash.service
    instantbridge-usb0-rearm.service
    instantbridge-usb0-lost.service
  )

  if [[ "${LIVE_ROOT}" -eq 1 ]]; then
    systemctl disable --now "${legacy_units[@]}" >/dev/null 2>&1 || true
    return
  fi

  systemctl --root="${ROOT%/}" disable "${legacy_units[@]}" >/dev/null 2>&1 || true
}

remove_slow_boot_display_defaults() {
  local config_txt="$1"
  local tmp
  tmp="$(mktemp)"
  awk '
    $0 == "camera_auto_detect=1" { next }
    $0 == "display_auto_detect=1" { next }
    $0 == "auto_initramfs=1" { next }
    $0 == "dtoverlay=vc4-kms-v3d" { next }
    $0 == "dtoverlay=vc4-fkms-v3d" { next }
    $0 == "max_framebuffers=2" { next }
    $0 == "disable_fw_kms_setup=1" { next }
    { print }
  ' "${config_txt}" > "${tmp}"
  cat "${tmp}" > "${config_txt}"
  rm -f "${tmp}"
}

echo "Installing InstantLink Bridge USB gadget config into ${ROOT}"
ensure_runtime_identity
disable_legacy_instantbridge_services
install_file config/g_ether.conf /etc/modprobe.d/g_ether.conf
install_file config/10-usb0.network /etc/systemd/network/10-usb0.network
install_file config/dnsmasq-bridge.conf /etc/dnsmasq.d/instantlink-bridge.conf
install_file config/99-networkmanager-unmanaged-usb0.conf \
  /etc/NetworkManager/conf.d/99-instantlink-bridge-unmanaged-usb0.conf
rm -f "${ROOT%/}/etc/systemd/journald.conf.d/10-instantlink-bridge-persistent.conf"
install_file config/journald-instantlink-bridge.conf \
  /etc/systemd/journald.conf.d/99-instantlink-bridge-persistent.conf
install -d -m 0755 "${ROOT%/}/var/log/journal"
tighten_netplan_permissions
install_private_file config/NetworkManager/instantlink-bridge-hotspot.nmconnection \
  /etc/NetworkManager/system-connections/InstantLink Bridge-Hotspot.nmconnection
install_executable scripts/wifi-mode.sh /usr/local/sbin/instantlink-bridge-wifi-mode
install_executable scripts/poweroff.sh /usr/local/sbin/instantlink-bridge-poweroff
install_executable scripts/usb-gadget-mode.sh /usr/local/sbin/instantlink-bridge-usb-gadget-mode
install_sudoers_file config/sudoers-instantlink-bridge-wifi /etc/sudoers.d/instantlink-bridge-wifi
install_sudoers_file config/sudoers-instantlink-bridge-power /etc/sudoers.d/instantlink-bridge-power
install_file udev/99-instantlink-bridge-usb0.rules /etc/udev/rules.d/99-instantlink-bridge-usb0.rules
install_file systemd/instantlink-bridge.service /etc/systemd/system/instantlink-bridge.service
install_file systemd/instantlink-bridge-boot-splash.service \
  /etc/systemd/system/instantlink-bridge-boot-splash.service
install_file systemd/instantlink-bridge-manager.service \
  /etc/systemd/system/instantlink-bridge-manager.service
install_file systemd/instantlink-bridge-usb0-rearm.service \
  /etc/systemd/system/instantlink-bridge-usb0-rearm.service
install_file systemd/instantlink-bridge-usb0-lost.service \
  /etc/systemd/system/instantlink-bridge-usb0-lost.service
if [[ ! -f "${ROOT%/}/etc/InstantLinkBridge/config.toml" ]]; then
  install_file config/config.example.toml /etc/InstantLinkBridge/config.toml
else
  echo "Preserved existing /etc/InstantLinkBridge/config.toml"
fi
chmod 2770 "${ROOT%/}/etc/InstantLinkBridge"
chmod 0660 "${ROOT%/}/etc/InstantLinkBridge/config.toml"
chgrp_runtime "${ROOT%/}/etc/InstantLinkBridge" "${ROOT%/}/etc/InstantLinkBridge/config.toml"
ensure_state_dirs
CONFIG_FILE="${ROOT%/}/etc/InstantLinkBridge/config.toml"
FTP_USERNAME="$(sed -n 's/^username = "\(.*\)"/\1/p' "${CONFIG_FILE}" |
  head -1)"
if [[ -z "${FTP_USERNAME}" || "${FTP_USERNAME}" == "instax" ]]; then
  tmp_config="$(mktemp)"
  if grep -q '^username = ' "${CONFIG_FILE}"; then
    awk '
      /^username = / { print "username = \"ib\""; next }
      { print }
    ' "${CONFIG_FILE}" > "${tmp_config}"
  else
    awk '
      { print }
      /^\[ftp\]$/ { print "username = \"ib\"" }
    ' "${CONFIG_FILE}" > "${tmp_config}"
  fi
  cat "${tmp_config}" > "${CONFIG_FILE}"
  rm -f "${tmp_config}"
fi
FTP_PASSWORD="$(sed -n 's/^password = "\(.*\)"/\1/p' "${CONFIG_FILE}" |
  head -1)"
if [[ -z "${FTP_PASSWORD}" || "${FTP_PASSWORD}" == "change-me" || "${FTP_PASSWORD}" == "instax" ]]; then
  FTP_PASSWORD="$(od -An -N4 -tu4 /dev/urandom |
    awk '{ printf "%08d\n", $1 % 100000000 }')"
  tmp_config="$(mktemp)"
  if grep -q '^password = ' "${CONFIG_FILE}"; then
    awk -v password="${FTP_PASSWORD}" '
      /^password = / { print "password = \"" password "\""; next }
      { print }
    ' "${CONFIG_FILE}" > "${tmp_config}"
  else
    awk -v password="${FTP_PASSWORD}" '
      { print }
      /^username = / { print "password = \"" password "\"" }
    ' "${CONFIG_FILE}" > "${tmp_config}"
  fi
  cat "${tmp_config}" > "${CONFIG_FILE}"
  rm -f "${tmp_config}"
fi

default_hotspot_ssid() {
  local machine_id
  local machine_id_path
  for machine_id_path in "${ROOT%/}/etc/machine-id" "${ROOT%/}/var/lib/dbus/machine-id"; do
    if [[ -r "${machine_id_path}" ]]; then
      machine_id="$(sed -n '1p' "${machine_id_path}" | tr '[:lower:]' '[:upper:]')"
      if [[ "${machine_id}" =~ ^[0-9A-F]{8} ]]; then
        # Last 4 hex chars of the device suffix — shorter SSID, matches
        # the InstantLink-XXXX format the Python default_hotspot_ssid uses.
        printf 'InstantLink-%s\n' "${machine_id:4:4}"
        return
      fi
    fi
  done
  printf '%s\n' 'InstantLink-XXXX'
}

HOTSPOT_SSID_FILE="${ROOT%/}/etc/InstantLinkBridge/hotspot.ssid"
HOTSPOT_PSK_FILE="${ROOT%/}/etc/InstantLinkBridge/hotspot.psk"
# Only the InstantLink-XXXX format is accepted; any older format
# (rewritten or otherwise) gets replaced with the new default on next
# provisioning pass.
if [[ ! -f "${HOTSPOT_SSID_FILE}" ]] ||
  ! sed -n '1p' "${HOTSPOT_SSID_FILE}" | grep -Eq '^InstantLink-[0-9A-F]{4}$'; then
  printf '%s\n' "$(default_hotspot_ssid)" > "${HOTSPOT_SSID_FILE}"
fi
if [[ ! -f "${HOTSPOT_PSK_FILE}" ]] ||
  ! sed -n '1p' "${HOTSPOT_PSK_FILE}" | grep -Eq '^[0-9]{8}$'; then
  od -An -N4 -tu4 /dev/urandom |
    awk '{ printf "%08d\n", $1 % 100000000 }' > "${HOTSPOT_PSK_FILE}"
fi
chmod 0644 "${HOTSPOT_SSID_FILE}"
chmod 0640 "${HOTSPOT_PSK_FILE}"
chgrp_runtime "${HOTSPOT_SSID_FILE}" "${HOTSPOT_PSK_FILE}"

CONFIG_TXT="${ROOT%/}/boot/firmware/config.txt"
CMDLINE_TXT="${ROOT%/}/boot/firmware/cmdline.txt"

if [[ -f "${CONFIG_TXT}" ]]; then
  remove_slow_boot_display_defaults "${CONFIG_TXT}"
  while IFS= read -r line; do
    [[ -z "${line}" || "${line}" =~ ^# ]] && continue
    grep -qxF "${line}" "${CONFIG_TXT}" || printf '\n%s\n' "${line}" >> "${CONFIG_TXT}"
  done < "${REPO_DIR}/config/boot-firmware-config.append"
else
  echo "WARN: ${CONFIG_TXT} not found; skipped boot config" >&2
fi

if [[ -f "${CMDLINE_TXT}" ]]; then
  token="$(<"${REPO_DIR}/config/cmdline-token.txt")"
  if ! tr ' ' '\n' < "${CMDLINE_TXT}" | grep -qxF "${token}"; then
    tmp="$(mktemp)"
    awk -v token="${token}" '{ print $0 " " token }' "${CMDLINE_TXT}" > "${tmp}"
    cat "${tmp}" > "${CMDLINE_TXT}"
    rm -f "${tmp}"
  fi
else
  echo "WARN: ${CMDLINE_TXT} not found; skipped cmdline token" >&2
fi

enable_service

echo "Done. Review config, then reboot target hardware."
