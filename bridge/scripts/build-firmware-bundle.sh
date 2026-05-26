#!/usr/bin/env bash
set -euo pipefail

BRIDGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(cd "${BRIDGE_ROOT}/.." && pwd)"
TARGET_TRIPLE="${INSTANTLINK_BRIDGE_RUST_TARGET:-aarch64-unknown-linux-gnu}"
DIST_DIR="${INSTANTLINK_BRIDGE_FIRMWARE_DIST_DIR:-${ROOT}/target/bridge-firmware/dist}"
STAGE_ROOT="${INSTANTLINK_BRIDGE_FIRMWARE_STAGE_ROOT:-${ROOT}/target/bridge-firmware/stage}"
APP_BUNDLE_DIR="${INSTANTLINK_BRIDGE_FIRMWARE_APP_BUNDLE_DIR:-${ROOT}/target/bridge-firmware/app-bundle/BridgeFirmware}"
BUILD_NATIVE="${INSTANTLINK_BRIDGE_BUILD_NATIVE:-1}"
ARTIFACT_DIR="${INSTANTLINK_BRIDGE_INSTANTLINK_ARTIFACT_DIR:-${ROOT}/target/${TARGET_TRIPLE}/release}"
ARTIFACT_MANIFEST_NAME="${INSTANTLINK_BRIDGE_ARTIFACTS_MANIFEST_NAME:-instantlink-artifacts-manifest.json}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SIGNING_PRIVATE_KEY="${INSTANTLINK_BRIDGE_FIRMWARE_SIGNING_KEY:-}"
SIGNING_KEY_ID="${INSTANTLINK_BRIDGE_FIRMWARE_SIGNING_KEY_ID:-}"
SIGNING_KEY_PASSWORD_ENV="${INSTANTLINK_BRIDGE_FIRMWARE_SIGNING_KEY_PASSWORD_ENV:-}"

usage() {
  cat <<'USAGE'
Usage: build-firmware-bundle.sh <version-or-tag>

Builds a self-contained InstantLink Bridge firmware bundle for Raspberry Pi OS arm64.

Examples:
  bridge/scripts/build-firmware-bundle.sh 0.1.0
  bridge/scripts/build-firmware-bundle.sh bridge-v0.1.0
  INSTANTLINK_BRIDGE_BUILD_NATIVE=0 bridge/scripts/build-firmware-bundle.sh 0.1.0

Environment overrides:
  INSTANTLINK_BRIDGE_BUILD_NATIVE          Build Linux arm64 CLI/FFI first, default 1
  INSTANTLINK_BRIDGE_INSTANTLINK_ARTIFACT_DIR
                                           Existing native artifact dir when build is skipped
  INSTANTLINK_BRIDGE_FIRMWARE_DIST_DIR     Output dir, default target/bridge-firmware/dist
  INSTANTLINK_BRIDGE_FIRMWARE_APP_BUNDLE_DIR
                                           App resource staging dir
  INSTANTLINK_BRIDGE_RUST_TARGET           Rust target, default aarch64-unknown-linux-gnu
  INSTANTLINK_BRIDGE_FIRMWARE_SIGNING_KEY  Optional Ed25519 private key path for signed builds
  INSTANTLINK_BRIDGE_FIRMWARE_SIGNING_KEY_ID
                                           Optional key id written into signature sidecars
  INSTANTLINK_BRIDGE_FIRMWARE_SIGNING_KEY_PASSWORD_ENV
                                           Optional env var containing encrypted key password
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

utc_now() {
  date -u '+%Y-%m-%dT%H:%M:%SZ'
}

sha256_file() {
  local path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${path}" | awk '{ print $1 }'
    return
  fi
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "${path}" | awk '{ print $1 }'
    return
  fi

  echo "ERROR: sha256sum or shasum is required" >&2
  exit 1
}

normalize_version() {
  local value="$1"
  value="${value#refs/tags/}"
  value="${value#bridge-v}"
  value="${value#v}"
  if [[ ! "${value}" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-.+][0-9A-Za-z.-]+)?$ ]]; then
    echo "ERROR: invalid firmware version '${1}'. Expected MAJOR.MINOR.PATCH." >&2
    exit 2
  fi
  printf '%s' "${value}"
}

git_branch_name() {
  local branch
  if branch="$(git -C "${ROOT}" symbolic-ref --quiet --short HEAD 2>/dev/null)"; then
    printf '%s' "${branch}"
    return
  fi
  printf 'DETACHED:%s' "$(git -C "${ROOT}" rev-parse --short HEAD)"
}

git_dirty_json_bool() {
  if [[ -n "$(git -C "${ROOT}" status --porcelain --untracked-files=all)" ]]; then
    printf 'true'
  else
    printf 'false'
  fi
}

copy_if_exists() {
  local source="$1"
  local dest="$2"
  if [[ -e "${source}" ]]; then
    mkdir -p "$(dirname "${dest}")"
    cp -R "${source}" "${dest}"
  fi
}

create_clean_tar() {
  local source_dir="$1"
  local archive="$2"

  if COPYFILE_DISABLE=1 tar --no-xattrs --disable-copyfile -C "${source_dir}" -czf "${archive}" . \
    2>/dev/null; then
    return
  fi
  rm -f "${archive}"
  if COPYFILE_DISABLE=1 tar --disable-copyfile -C "${source_dir}" -czf "${archive}" . \
    2>/dev/null; then
    return
  fi
  rm -f "${archive}"
  COPYFILE_DISABLE=1 tar -C "${source_dir}" -czf "${archive}" .
}

sign_manifest_sidecar() {
  local manifest_path="$1"
  local signature_path="$2"

  if [[ -z "${SIGNING_PRIVATE_KEY}" ]]; then
    return 0
  fi
  if [[ "$(git_dirty_json_bool)" == "true" ]]; then
    echo "ERROR: refusing to sign firmware bundle from a dirty worktree" >&2
    exit 1
  fi
  if [[ ! -f "${SIGNING_PRIVATE_KEY}" ]]; then
    echo "ERROR: firmware signing key does not exist: ${SIGNING_PRIVATE_KEY}" >&2
    exit 1
  fi

  local args=(
    "${BRIDGE_ROOT}/scripts/sign-firmware-manifest.py"
    "${manifest_path}"
    --private-key "${SIGNING_PRIVATE_KEY}"
    --output "${signature_path}"
  )
  if [[ -n "${SIGNING_KEY_ID}" ]]; then
    args+=(--key-id "${SIGNING_KEY_ID}")
  fi
  if [[ -n "${SIGNING_KEY_PASSWORD_ENV}" ]]; then
    args+=(--private-key-pass-env "${SIGNING_KEY_PASSWORD_ENV}")
  fi

  "${PYTHON_BIN}" "${args[@]}"
}

render_bundle_manifest() {
  local output_path="$1"
  local version="$2"
  local source_ref="$3"
  local archive_basename="$4"
  local package_dir="$5"
  local cli_source="$6"
  local lib_source="$7"
  local artifacts_manifest="$8"

  "${PYTHON_BIN}" - \
    "${output_path}" \
    "${version}" \
    "${source_ref}" \
    "$(utc_now)" \
    "${archive_basename}.tar.gz" \
    "${TARGET_TRIPLE}" \
    "$(git -C "${ROOT}" rev-parse --verify HEAD)" \
    "$(git_branch_name)" \
    "$(git_dirty_json_bool)" \
    "$(sha256_file "${cli_source}")" \
    "$(sha256_file "${lib_source}")" \
    "$(sha256_file "${artifacts_manifest}")" \
    "${package_dir}" <<'PY'
import json
import pathlib
import re
import sys

(
    output_path,
    version,
    source_ref,
    built_at,
    archive_name,
    target_triple,
    commit_sha,
    branch,
    dirty,
    cli_sha,
    lib_sha,
    artifacts_manifest_sha,
    package_dir,
) = sys.argv[1:]

bridge_root = pathlib.Path(package_dir) / "bridge"
pyproject_text = (bridge_root / "pyproject.toml").read_text(encoding="utf-8")

def project_value(name):
    pattern = rf"^{re.escape(name)}\s*=\s*\"([^\"]+)\""
    match = re.search(pattern, pyproject_text, flags=re.MULTILINE)
    return match.group(1) if match else "unknown"

manifest = {
    "schema_version": 1,
    "package_kind": "instantlink_bridge_firmware",
    "bridge_version": version,
    "source_ref": source_ref,
    "built_at_utc": built_at,
    "required_bridge_api_version": 1,
    "migration_notes": [],
    "minimum_rollback_version": None,
    "target": {
        "platform": "linux",
        "architecture": "aarch64",
        "rust_triple": target_triple,
        "raspberry_pi_os": "Debian 13 / Trixie arm64",
    },
    "archive": {
        "name": archive_name,
        "compression": "gzip",
    },
    "python": {
        "package": project_value("name"),
        "package_version": project_value("version"),
        "requires_python": project_value("requires-python"),
        "constraints": "bridge/requirements/constraints.txt",
    },
    "instantlink_workspace": {
        "commit_sha": commit_sha,
        "branch": branch,
        "dirty": dirty == "true",
    },
    "native_artifacts": {
        "instantlink": {
            "path": "native/bin/instantlink",
            "sha256": cli_sha,
        },
        "libinstantlink_ffi.so": {
            "path": "native/lib/libinstantlink_ffi.so",
            "sha256": lib_sha,
        },
        "build_manifest": {
            "path": "native/instantlink-artifacts-manifest.json",
            "sha256": artifacts_manifest_sha,
        },
    },
    "install": {
        "script": "install-firmware-bundle.sh",
        "default_target": "/opt/InstantLinkBridge",
        "systemd_service": "instantlink-bridge.service",
    },
}

pathlib.Path(output_path).write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
}

render_checksums() {
  local package_dir="$1"
  "${PYTHON_BIN}" - "${package_dir}" <<'PY'
import hashlib
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
lines = []
for path in sorted(root.rglob("*")):
    if not path.is_file():
        continue
    rel = path.relative_to(root)
    if rel.as_posix() == "SHA256SUMS":
        continue
    lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {rel.as_posix()}")
(root / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

render_latest_json() {
  local output_path="$1"
  local version="$2"
  local archive_path="$3"
  local manifest_path="$4"
  local checksum_path="$5"

  "${PYTHON_BIN}" - \
    "${output_path}" \
    "${version}" \
    "$(basename "${archive_path}")" \
    "$(sha256_file "${archive_path}")" \
    "$(basename "${manifest_path}")" \
    "$(sha256_file "${manifest_path}")" \
    "$(basename "${checksum_path}")" \
    "$(sha256_file "${checksum_path}")" \
    "$(utc_now)" <<'PY'
import json
import pathlib
import sys

(
    output_path,
    version,
    archive_name,
    archive_sha,
    manifest_name,
    manifest_sha,
    checksum_name,
    checksum_sha,
    staged_at,
) = sys.argv[1:]

payload = {
    "schema_version": 1,
    "package_kind": "instantlink_bridge_firmware",
    "bridge_version": version,
    "required_bridge_api_version": 1,
    "migration_notes": [],
    "minimum_rollback_version": None,
    "instantlink_workspace": {
        "commit_sha": "0" * 40,
        "branch": "release-index",
        "dirty": False,
    },
    "target": "linux-aarch64",
    "archive_name": archive_name,
    "archive_sha256": archive_sha,
    "manifest_name": manifest_name,
    "manifest_sha256": manifest_sha,
    "checksum_name": checksum_name,
    "checksum_sha256": checksum_sha,
    "staged_at_utc": staged_at,
}

pathlib.Path(output_path).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
}

main() {
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
  fi

  local input_version="${1:-}"
  if [[ -z "${input_version}" ]]; then
    usage >&2
    exit 2
  fi

  local version
  version="$(normalize_version "${input_version}")"
  local tag_label="v${version}"
  local archive_basename="InstantLinkBridgeFirmware-${tag_label}-linux-aarch64"
  local package_dir="${STAGE_ROOT}/${archive_basename}"
  local archive_path="${DIST_DIR}/${archive_basename}.tar.gz"
  local manifest_dist_path="${DIST_DIR}/${archive_basename}.manifest.json"
  local manifest_sig_path="${DIST_DIR}/${archive_basename}.manifest.sig"
  local checksum_path="${archive_path}.sha256"
  local latest_path="${DIST_DIR}/latest.json"
  local latest_sig_path="${DIST_DIR}/latest.json.sig"

  if is_truthy "${BUILD_NATIVE}"; then
    "${BRIDGE_ROOT}/scripts/build-instantlink-artifacts.sh"
  fi

  local cli_source="${ARTIFACT_DIR}/instantlink"
  local lib_source="${ARTIFACT_DIR}/libinstantlink_ffi.so"
  local artifacts_manifest="${ARTIFACT_DIR}/${ARTIFACT_MANIFEST_NAME}"
  for required in "${cli_source}" "${lib_source}" "${artifacts_manifest}"; do
    if [[ ! -f "${required}" ]]; then
      echo "ERROR: missing native artifact: ${required}" >&2
      exit 1
    fi
  done

  rm -rf "${package_dir}" "${DIST_DIR}" "${APP_BUNDLE_DIR}"
  mkdir -p "${package_dir}/bridge" "${package_dir}/native/bin" "${package_dir}/native/lib" \
    "${DIST_DIR}" "${APP_BUNDLE_DIR}"

  for directory in src config systemd udev scripts docs boot requirements; do
    copy_if_exists "${BRIDGE_ROOT}/${directory}" "${package_dir}/bridge/${directory}"
  done
  for file in README.md CLAUDE.md ARCHITECTURE.md HARDWARE.md DECISIONS.md ROADMAP.md pyproject.toml; do
    copy_if_exists "${BRIDGE_ROOT}/${file}" "${package_dir}/bridge/${file}"
  done
  find "${package_dir}/bridge" \
    \( -name __pycache__ -o -name .mypy_cache -o -name .pytest_cache -o -name .ruff_cache \) \
    -type d -prune -exec rm -rf {} +
  find "${package_dir}/bridge" \
    \( -name '*.pyc' -o -name '*.pyo' -o -name '.DS_Store' -o -name '.coverage' \) \
    -type f -delete

  install -m 0755 "${cli_source}" "${package_dir}/native/bin/instantlink"
  install -m 0755 "${lib_source}" "${package_dir}/native/lib/libinstantlink_ffi.so"
  install -m 0644 "${artifacts_manifest}" "${package_dir}/native/instantlink-artifacts-manifest.json"
  install -m 0755 "${BRIDGE_ROOT}/scripts/install-firmware-bundle.sh" \
    "${package_dir}/install-firmware-bundle.sh"

  render_bundle_manifest \
    "${package_dir}/manifest.json" \
    "${version}" \
    "${input_version}" \
    "${archive_basename}" \
    "${package_dir}" \
    "${cli_source}" \
    "${lib_source}" \
    "${artifacts_manifest}"
  render_checksums "${package_dir}"

  create_clean_tar "${package_dir}" "${archive_path}"
  printf '%s  %s\n' "$(sha256_file "${archive_path}")" "$(basename "${archive_path}")" \
    > "${checksum_path}"
  cp "${package_dir}/manifest.json" "${manifest_dist_path}"
  cp "${archive_path}" "${checksum_path}" "${manifest_dist_path}" "${APP_BUNDLE_DIR}/"
  sign_manifest_sidecar "${manifest_dist_path}" "${manifest_sig_path}"
  if [[ -f "${manifest_sig_path}" ]]; then
    cp "${manifest_sig_path}" "${APP_BUNDLE_DIR}/"
  fi
  render_latest_json \
    "${latest_path}" \
    "${version}" \
    "${archive_path}" \
    "${manifest_dist_path}" \
    "${checksum_path}"
  cp "${latest_path}" "${APP_BUNDLE_DIR}/latest.json"
  sign_manifest_sidecar "${latest_path}" "${latest_sig_path}"
  if [[ -f "${latest_sig_path}" ]]; then
    cp "${latest_sig_path}" "${APP_BUNDLE_DIR}/"
  fi

  printf 'Bridge firmware bundle ready: %s\n' "${archive_path}"
  if [[ -n "${SIGNING_PRIVATE_KEY}" ]]; then
    printf 'Bridge firmware signatures ready: %s, %s\n' "${manifest_sig_path}" "${latest_sig_path}"
  fi
  printf 'App bundle staging ready: %s\n' "${APP_BUNDLE_DIR}"
}

main "$@"
