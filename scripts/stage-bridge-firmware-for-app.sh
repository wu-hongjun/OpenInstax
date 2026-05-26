#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE_DIR="${INSTANTLINK_BRIDGE_FIRMWARE_SOURCE_DIR:-${ROOT}/target/bridge-firmware/dist}"
DEST_DIR="${INSTANTLINK_BRIDGE_FIRMWARE_APP_BUNDLE_DIR:-${ROOT}/target/bridge-firmware/app-bundle/BridgeFirmware}"
VERSION=""

usage() {
  cat <<'USAGE'
Usage: scripts/stage-bridge-firmware-for-app.sh [--version <version-or-tag>] [--from-dir <dir>]

Stages an already-built InstantLink Bridge firmware bundle so scripts/build-app.sh can copy it into
InstantLink.app/Contents/Resources/BridgeFirmware.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)
      VERSION="${2:?--version requires a value}"
      shift 2
      ;;
    --from-dir)
      SOURCE_DIR="${2:?--from-dir requires a value}"
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! -d "${SOURCE_DIR}" ]]; then
  echo "ERROR: firmware source directory does not exist: ${SOURCE_DIR}" >&2
  exit 1
fi

normalized="${VERSION#refs/tags/}"
normalized="${normalized#bridge-v}"
normalized="${normalized#v}"

if [[ -n "${VERSION}" ]]; then
  archive="${SOURCE_DIR}/InstantLinkBridgeFirmware-v${normalized}-linux-aarch64.tar.gz"
else
  archive="$(find "${SOURCE_DIR}" -maxdepth 1 -name 'InstantLinkBridgeFirmware-v*-linux-aarch64.tar.gz' -print | sort | tail -n 1)"
fi

if [[ -z "${archive}" || ! -f "${archive}" ]]; then
  echo "ERROR: no matching firmware archive found in ${SOURCE_DIR}" >&2
  exit 1
fi

basename="$(basename "${archive}")"
manifest="${SOURCE_DIR}/${basename%.tar.gz}.manifest.json"
checksum="${archive}.sha256"
latest="${SOURCE_DIR}/latest.json"

for required in "${manifest}" "${checksum}" "${latest}"; do
  if [[ ! -f "${required}" ]]; then
    echo "ERROR: missing firmware sidecar: ${required}" >&2
    exit 1
  fi
done

rm -rf "${DEST_DIR}"
mkdir -p "${DEST_DIR}"
cp "${archive}" "${checksum}" "${manifest}" "${latest}" "${DEST_DIR}/"

printf 'Staged Bridge firmware for app resources at %s\n' "${DEST_DIR}"
