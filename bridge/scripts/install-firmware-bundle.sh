#!/usr/bin/env bash
set -euo pipefail

TARGET="${INSTANTLINK_BRIDGE_TARGET:-/opt/InstantLinkBridge}"
OWNER="${INSTANTLINK_BRIDGE_OWNER:-ib}"
GROUP="${INSTANTLINK_BRIDGE_GROUP:-ib}"
RESTART="${INSTANTLINK_BRIDGE_RESTART:-1}"
INSTALL_DEPS="${INSTANTLINK_BRIDGE_INSTALL_DEPS:-0}"
VERIFY_CHECKSUMS="${INSTANTLINK_BRIDGE_VERIFY_CHECKSUMS:-1}"
DEPLOY_METADATA_DIR="${INSTANTLINK_BRIDGE_DEPLOY_METADATA_DIR:-${TARGET}/.deployment}"

usage() {
  cat <<'USAGE'
Usage: install-firmware-bundle.sh <extracted-firmware-bundle-dir>

Installs a packaged InstantLink Bridge firmware bundle on a Raspberry Pi.

Environment overrides:
  INSTANTLINK_BRIDGE_TARGET            Install root, default /opt/InstantLinkBridge
  INSTANTLINK_BRIDGE_OWNER             Runtime file owner, default ib
  INSTANTLINK_BRIDGE_GROUP             Runtime file group, default ib
  INSTANTLINK_BRIDGE_RESTART           Restart service after install, default 1
  INSTANTLINK_BRIDGE_INSTALL_DEPS      Reinstall Python deps from constraints, default 0
  INSTANTLINK_BRIDGE_VERIFY_CHECKSUMS  Verify SHA256SUMS before install, default 1
USAGE
}

is_truthy() {
  local normalized
  normalized="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  case "${normalized}" in
    0 | false | no | off)
      return 1
      ;;
    *)
      return 0
      ;;
  esac
}

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    echo "ERROR: run as root so service files, native libraries, and ownership can be updated" >&2
    exit 1
  fi
}

verify_checksums() {
  local bundle_dir="$1"
  local sums="${bundle_dir}/SHA256SUMS"

  if ! is_truthy "${VERIFY_CHECKSUMS}"; then
    return
  fi
  if [[ ! -f "${sums}" ]]; then
    echo "ERROR: missing checksum file: ${sums}" >&2
    exit 1
  fi

  if command -v sha256sum >/dev/null 2>&1; then
    (cd "${bundle_dir}" && sha256sum -c SHA256SUMS)
    return
  fi
  if command -v shasum >/dev/null 2>&1; then
    (cd "${bundle_dir}" && shasum -a 256 -c SHA256SUMS)
    return
  fi

  echo "ERROR: sha256sum or shasum is required to verify firmware bundles" >&2
  exit 1
}

ensure_runtime_identity() {
  if ! getent group "${GROUP}" >/dev/null 2>&1; then
    groupadd --system "${GROUP}"
  fi
  if ! id -u "${OWNER}" >/dev/null 2>&1; then
    useradd --system --gid "${GROUP}" --home-dir /var/lib/InstantLinkBridge \
      --no-create-home --shell /usr/sbin/nologin "${OWNER}"
  fi
  for supplementary in bluetooth gpio spi i2c plugdev video; do
    if getent group "${supplementary}" >/dev/null 2>&1; then
      usermod -aG "${supplementary}" "${OWNER}"
    fi
  done
}

sync_bridge_tree() {
  local source="$1"

  install -d -o "${OWNER}" -g "${GROUP}" "${TARGET}"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
      --exclude .venv \
      --exclude .deployment \
      --exclude lib \
      --exclude bin \
      "${source}/" "${TARGET}/"
  else
    tar -C "${source}" -cf - . | tar -C "${TARGET}" -xf -
    rm -rf "${TARGET}/target" "${TARGET}/.ruff_cache" "${TARGET}/.mypy_cache"
  fi
}

install_native_artifacts() {
  local bundle_dir="$1"
  local cli="${bundle_dir}/native/bin/instantlink"
  local lib="${bundle_dir}/native/lib/libinstantlink_ffi.so"
  local artifact_manifest="${bundle_dir}/native/instantlink-artifacts-manifest.json"

  if [[ ! -f "${cli}" || ! -f "${lib}" || ! -f "${artifact_manifest}" ]]; then
    echo "ERROR: firmware bundle is missing native InstantLink artifacts" >&2
    exit 1
  fi

  install -D -m 0755 -o "${OWNER}" -g "${GROUP}" "${cli}" "${TARGET}/bin/instantlink"
  install -D -m 0755 -o "${OWNER}" -g "${GROUP}" "${lib}" "${TARGET}/lib/libinstantlink_ffi.so"
  install -D -m 0644 -o "${OWNER}" -g "${GROUP}" \
    "${artifact_manifest}" "${DEPLOY_METADATA_DIR}/instantlink-artifacts-manifest.json"
}

install_metadata() {
  local bundle_dir="$1"
  install -d -o "${OWNER}" -g "${GROUP}" "${DEPLOY_METADATA_DIR}"
  install -m 0644 -o "${OWNER}" -g "${GROUP}" \
    "${bundle_dir}/manifest.json" "${DEPLOY_METADATA_DIR}/firmware-bundle-manifest.json"
  install -m 0644 -o "${OWNER}" -g "${GROUP}" \
    "${bundle_dir}/SHA256SUMS" "${DEPLOY_METADATA_DIR}/firmware-bundle-SHA256SUMS"
}

install_python_deps() {
  if ! is_truthy "${INSTALL_DEPS}"; then
    return
  fi
  "${TARGET}/scripts/install-runtime-deps.sh"
}

restart_service() {
  systemctl daemon-reload
  if is_truthy "${RESTART}"; then
    systemctl restart instantlink-bridge.service
  fi
}

main() {
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
  fi

  local bundle_dir="${1:-}"
  if [[ -z "${bundle_dir}" ]]; then
    usage >&2
    exit 2
  fi
  bundle_dir="$(cd "${bundle_dir}" && pwd)"

  for required in bridge manifest.json SHA256SUMS native/bin/instantlink native/lib/libinstantlink_ffi.so; do
    if [[ ! -e "${bundle_dir}/${required}" ]]; then
      echo "ERROR: missing ${required} in ${bundle_dir}" >&2
      exit 1
    fi
  done

  require_root
  verify_checksums "${bundle_dir}"
  ensure_runtime_identity
  sync_bridge_tree "${bundle_dir}/bridge"
  install_native_artifacts "${bundle_dir}"
  install_metadata "${bundle_dir}"
  chown -R "${OWNER}:${GROUP}" "${TARGET}"
  install_python_deps
  restart_service

  printf 'Installed InstantLink Bridge firmware from %s\n' "${bundle_dir}"
}

main "$@"
