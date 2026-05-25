#!/usr/bin/env bash
set -euo pipefail

HOST="${INSTANTLINK_BRIDGE_HOST:-}"
USER="${INSTANTLINK_BRIDGE_USER:-}"
TARGET="${INSTANTLINK_BRIDGE_TARGET:-/opt/InstantLinkBridge}"
OWNER="${INSTANTLINK_BRIDGE_OWNER:-ib}"
GROUP="${INSTANTLINK_BRIDGE_GROUP:-ib}"
CONFIG_DIR="${INSTANTLINK_BRIDGE_CONFIG_DIR:-/etc/InstantLinkBridge}"
CONSTRAINTS_RELATIVE_PATH="${INSTANTLINK_BRIDGE_CONSTRAINTS_RELATIVE_PATH:-requirements/constraints.txt}"
DEPLOY_METADATA_DIR="${INSTANTLINK_BRIDGE_DEPLOY_METADATA_DIR:-${TARGET}/.deployment}"
DEPLOY_MANIFEST_PATH="${INSTANTLINK_BRIDGE_DEPLOY_MANIFEST_PATH:-${DEPLOY_METADATA_DIR}/deployment-manifest.json}"
RUNTIME_PACKAGES_ARTIFACT="${INSTANTLINK_BRIDGE_RUNTIME_PACKAGES_ARTIFACT:-${DEPLOY_METADATA_DIR}/runtime-installed-packages.txt}"
RUNTIME_APT_PACKAGES_ARTIFACT="${INSTANTLINK_BRIDGE_RUNTIME_APT_PACKAGES_ARTIFACT:-${DEPLOY_METADATA_DIR}/runtime-apt-packages.txt}"
RUNTIME_DEPS_MANIFEST="${INSTANTLINK_BRIDGE_RUNTIME_DEPS_MANIFEST:-${DEPLOY_METADATA_DIR}/runtime-deps-manifest.json}"
INSTANTLINK_ARTIFACTS_MANIFEST="${INSTANTLINK_BRIDGE_ARTIFACTS_MANIFEST:-${DEPLOY_METADATA_DIR}/instantlink-artifacts-manifest.json}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
TARGET_PYTHON_BIN="${TARGET_PYTHON_BIN:-python3}"
SSH_BIN="${SSH_BIN:-ssh}"
SCP_BIN="${SCP_BIN:-scp}"
RESTART=0
SYSTEM=0
INSTALL_DEPS=0
INSTALL_INSTANTLINK_ARTIFACTS=0
ALLOW_DIRTY=0
SSH_CMD=()
SCP_CMD=()

usage() {
  cat <<'USAGE'
Usage: scripts/deploy-to-pi.sh [--restart] [--system] [--deps] [--instantlink-artifacts] [--allow-dirty]

Environment overrides:
  INSTANTLINK_BRIDGE_HOST    SSH host, required
  INSTANTLINK_BRIDGE_USER    SSH user, required
  INSTANTLINK_BRIDGE_TARGET  Target directory, default /opt/InstantLinkBridge
  INSTANTLINK_BRIDGE_OWNER   Installed file owner, default ib
  INSTANTLINK_BRIDGE_GROUP   Installed file group, default ib
  INSTANTLINK_BRIDGE_CONFIG_DIR
                       Runtime config directory, default /etc/InstantLinkBridge
  INSTANTLINK_BRIDGE_DEPLOY_METADATA_DIR
                       Deploy/runtime metadata directory, default TARGET/.deployment
  INSTANTLINK_BRIDGE_DEPLOY_MANIFEST_PATH
                       Deployment manifest path, default metadata dir/deployment-manifest.json
  INSTANTLINK_BRIDGE_INSTANTLINK_ARTIFACT_DIR
                       Local directory containing cross-built InstantLink Linux arm64 artifacts,
                       default ../target/aarch64-unknown-linux-gnu/release
  SSHPASS              Optional SSH password. When set, ssh/scp are invoked via
                       sshpass -e for Pi setups that do not yet have keys.
  SSH_BIN              SSH command, default ssh
  SCP_BIN              SCP command, default scp
  TARGET_PYTHON_BIN    Pi runtime Python command for --deps, default python3

By default this refuses dirty working trees and deploys a git archive of the committed bridge
source without .git metadata. With --allow-dirty it copies the current working tree and records
dirty=true in the
deployment manifest. It does not install Python packages; use it for
source/config/docs updates after the Pi has been provisioned. Use --deps when
pyproject.toml or requirements/constraints.txt changed.

Options:
  --restart      Restart instantlink-bridge.service after copying source.
  --system       Re-run the idempotent live system provisioning script from TARGET.
  --deps         Reinstall runtime Python dependencies before any restart.
  --instantlink-artifacts
                 Copy prebuilt InstantLink FFI/CLI artifacts into TARGET/lib and TARGET/bin.
                 Use this when the Pi is in hotspot mode and cannot cargo-build from source.
  --allow-dirty  Deploy the current dirty working tree and mark the manifest dirty.
USAGE
}

init_ssh_commands() {
  SSH_CMD=("${SSH_BIN}")
  SCP_CMD=("${SCP_BIN}")

  if [[ -n "${SSHPASS:-}" ]]; then
    if ! command -v sshpass >/dev/null 2>&1; then
      echo "ERROR: SSHPASS is set but sshpass is not installed" >&2
      exit 1
    fi
    SSH_CMD=(sshpass -e "${SSH_BIN}")
    SCP_CMD=(sshpass -e "${SCP_BIN}")
  fi
}

require_deploy_target() {
  if [[ -z "${HOST}" || -z "${USER}" ]]; then
    echo "ERROR: set INSTANTLINK_BRIDGE_HOST and INSTANTLINK_BRIDGE_USER before deploying" >&2
    exit 2
  fi
}

sha256_file() {
  local path="$1"
  if [[ ! -f "${path}" ]]; then
    printf ''
    return 0
  fi

  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${path}" | awk '{ print $1 }'
    return 0
  fi
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "${path}" | awk '{ print $1 }'
    return 0
  fi

  echo "ERROR: sha256sum or shasum is required to render deployment metadata" >&2
  return 1
}

utc_now() {
  date -u '+%Y-%m-%dT%H:%M:%SZ'
}

git_branch_name() {
  local repo="$1"
  local branch
  if branch="$(git -C "${repo}" symbolic-ref --quiet --short HEAD 2>/dev/null)"; then
    printf '%s' "${branch}"
    return 0
  fi
  printf 'DETACHED:%s' "$(git -C "${repo}" rev-parse --short HEAD)"
}

git_remote_url() {
  local repo="$1"
  local branch="$2"
  local remote=""
  local url=""

  if [[ "${branch}" != DETACHED:* ]]; then
    remote="$(git -C "${repo}" config --get "branch.${branch}.remote" 2>/dev/null || true)"
  fi
  if [[ -n "${remote}" ]]; then
    url="$(git -C "${repo}" remote get-url "${remote}" 2>/dev/null || true)"
  fi
  if [[ -z "${url}" ]]; then
    url="$(git -C "${repo}" remote get-url origin 2>/dev/null || true)"
  fi
  printf '%s' "${url}"
}

git_worktree_dirty() {
  local repo="$1"
  [[ -n "$(git -C "${repo}" status --porcelain --untracked-files=all)" ]]
}

load_runtime_apt_packages() {
  local install_script="$1"
  if [[ ! -f "${install_script}" ]]; then
    return 0
  fi
  bash -c 'set -euo pipefail; source "$1"; printf "%s\n" "${APT_PACKAGES[@]}"' \
    bash "${install_script}"
}

render_deployment_manifest() {
  local output_path="$1"
  local commit_sha="$2"
  local dirty="$3"
  local branch="$4"
  local remote_url="$5"
  local deploy_timestamp="$6"
  local source_mode="$7"
  local constraints_source="$8"
  local constraints_target="$9"
  local pyproject_source="${10}"
  local install_script_source="${11}"
  local provision_script_source="${12}"
  local runtime_packages_artifact="${13}"
  local runtime_deps_manifest="${14}"
  local repo_root="${15:-$(pwd)}"
  local runtime_apt_packages_artifact="${16:-${DEPLOY_METADATA_DIR}/runtime-apt-packages.txt}"
  local instantlink_artifacts_manifest="${17:-${DEPLOY_METADATA_DIR}/instantlink-artifacts-manifest.json}"

  local constraints_sha
  local pyproject_sha
  local install_script_sha
  local provision_script_sha
  local apt_packages_file

  constraints_sha="$(sha256_file "${constraints_source}")"
  pyproject_sha="$(sha256_file "${pyproject_source}")"
  install_script_sha="$(sha256_file "${install_script_source}")"
  provision_script_sha="$(sha256_file "${provision_script_source}")"
  apt_packages_file="$(mktemp -t instantlink-bridge-apt-packages.XXXXXX)"

  if declare -p APT_PACKAGES >/dev/null 2>&1; then
    printf '%s\n' "${APT_PACKAGES[@]}" > "${apt_packages_file}"
  else
    : > "${apt_packages_file}"
  fi

  "${PYTHON_BIN}" - \
    "${output_path}" \
    "${commit_sha}" \
    "${dirty}" \
    "${branch}" \
    "${remote_url}" \
    "${deploy_timestamp}" \
    "${source_mode}" \
    "${constraints_source}" \
    "${constraints_target}" \
    "${constraints_sha}" \
    "${pyproject_sha}" \
    "${install_script_sha}" \
    "${provision_script_sha}" \
    "${runtime_packages_artifact}" \
    "${runtime_deps_manifest}" \
    "${repo_root}" \
    "${runtime_apt_packages_artifact}" \
    "${instantlink_artifacts_manifest}" \
    "${apt_packages_file}" <<'PY'
import hashlib
import json
import pathlib
import sys

(
    output_path,
    commit_sha,
    dirty,
    branch,
    remote_url,
    deploy_timestamp,
    source_mode,
    constraints_source,
    constraints_target,
    constraints_sha,
    pyproject_sha,
    install_script_sha,
    provision_script_sha,
    runtime_packages_artifact,
    runtime_deps_manifest,
    repo_root,
    runtime_apt_packages_artifact,
    instantlink_artifacts_manifest,
    apt_packages_file,
) = sys.argv[1:]

apt_packages = [
    line.strip()
    for line in pathlib.Path(apt_packages_file).read_text(encoding="utf-8").splitlines()
    if line.strip()
]

root = pathlib.Path(repo_root)
fingerprint_paths = []
for directory in ("config", "systemd", "udev"):
    base = root / directory
    if base.exists():
        fingerprint_paths.extend(
            path for path in base.rglob("*") if path.is_file() and path.name != ".gitkeep"
        )
for relative in (
    "scripts/provision-sd.sh",
    "scripts/install-runtime-deps.sh",
    "scripts/boot-diet.sh",
    "scripts/wifi-mode.sh",
    "scripts/poweroff.sh",
):
    path = root / relative
    if path.exists():
        fingerprint_paths.append(path)

system_fingerprints = []
for path in sorted(set(fingerprint_paths)):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    system_fingerprints.append(
        {
            "path": str(path.relative_to(root)),
            "sha256": digest,
        }
    )

manifest = {
    "schema_version": 1,
    "commit_sha": commit_sha,
    "dirty": dirty == "true",
    "branch": branch,
    "remote_url": remote_url or None,
    "deployed_at_utc": deploy_timestamp,
    "source_mode": source_mode,
    "dependencies": {
        "python_constraints": {
            "source_path": constraints_source,
            "target_path": constraints_target,
            "sha256": constraints_sha or None,
        },
        "pyproject": {
            "source_path": "pyproject.toml",
            "sha256": pyproject_sha or None,
        },
        "runtime_install": {
            "script": "scripts/install-runtime-deps.sh",
            "script_sha256": install_script_sha or None,
            "installed_packages_artifact": runtime_packages_artifact,
            "apt_packages_artifact": runtime_apt_packages_artifact,
            "runtime_deps_manifest": runtime_deps_manifest,
        },
        "instantlink_artifacts": {
            "manifest": instantlink_artifacts_manifest,
        },
        "provision": {
            "script": "scripts/provision-sd.sh",
            "script_sha256": provision_script_sha or None,
            "apt_packages": apt_packages,
            "system_fingerprints": system_fingerprints,
        },
    },
}

pathlib.Path(output_path).write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
  rm -f "${apt_packages_file}"
}

create_commit_archive() {
  local repo="$1"
  local commit_sha="$2"
  local archive="$3"
  local top
  local prefix
  local temp_dir

  top="$(git -C "${repo}" rev-parse --show-toplevel)"
  prefix="$(git -C "${repo}" rev-parse --show-prefix)"
  temp_dir="$(mktemp -d -t instantlink-bridge-archive.XXXXXX)"

  git -C "${top}" archive --format=tar "${commit_sha}" -- "${prefix}" |
    tar -xf - -C "${temp_dir}"
  if [[ -n "${prefix}" ]]; then
    COPYFILE_DISABLE=1 tar -C "${temp_dir}/${prefix%/}" -czf "${archive}" .
  else
    COPYFILE_DISABLE=1 tar -C "${temp_dir}" -czf "${archive}" .
  fi
  rm -rf "${temp_dir}"
}

create_working_tree_archive() {
  local archive="$1"
  if COPYFILE_DISABLE=1 tar --no-xattrs --disable-copyfile "${EXCLUDES[@]}" -czf "${archive}" . 2>/dev/null; then
    return 0
  fi
  rm -f "${archive}"
  if COPYFILE_DISABLE=1 tar --disable-copyfile "${EXCLUDES[@]}" -czf "${archive}" . 2>/dev/null; then
    return 0
  fi
  rm -f "${archive}"
  COPYFILE_DISABLE=1 tar "${EXCLUDES[@]}" -czf "${archive}" .
}

deploy_archive_to_pi() {
  local archive="$1"
  local remote_archive="/tmp/instantlink-bridge-deploy.tar.gz"
  local staging="/tmp/instantlink-bridge-deploy-${USER}"

  "${SCP_CMD[@]}" -q "${archive}" "${USER}@${HOST}:${remote_archive}"
  if "${SSH_CMD[@]}" "${USER}@${HOST}" "command -v rsync >/dev/null"; then
    "${SSH_CMD[@]}" -t "${USER}@${HOST}" \
      "rm -rf '${staging}' && mkdir -p '${staging}' && \
       tar -xzf '${remote_archive}' -C '${staging}' && \
       sudo rsync -a --delete --exclude .venv --exclude .deployment '${staging}/' '${TARGET}/' && \
       rm -rf '${staging}' '${remote_archive}' && \
       sudo chown -R '${OWNER}:${GROUP}' '${TARGET}' && \
       sudo find '${TARGET}' -name '._*' -delete"
  else
    "${SSH_CMD[@]}" -t "${USER}@${HOST}" \
      "sudo mkdir -p '${TARGET}' && \
       sudo tar -xzf '${remote_archive}' -C '${TARGET}' --owner='${OWNER}' --group='${GROUP}' && \
       rm '${remote_archive}' && \
       sudo rm -rf '${TARGET}/.omc' '${TARGET}/target' && \
       sudo find '${TARGET}' -name '._*' -delete"
  fi
}

deploy_working_tree_to_pi() {
  if [[ -z "${SSHPASS:-}" ]] && command -v rsync >/dev/null && "${SSH_CMD[@]}" "${USER}@${HOST}" "command -v rsync >/dev/null"; then
    local staging="/tmp/instantlink-bridge-deploy-${USER}"
    "${SSH_CMD[@]}" "${USER}@${HOST}" "rm -rf '${staging}' && mkdir -p '${staging}'"
    rsync -az --delete "${EXCLUDES[@]}" ./ "${USER}@${HOST}:${staging}/"
    "${SSH_CMD[@]}" -t "${USER}@${HOST}" \
      "sudo rsync -a --delete --exclude .venv --exclude .deployment '${staging}/' '${TARGET}/' && \
       rm -rf '${staging}' && \
       sudo chown -R '${OWNER}:${GROUP}' '${TARGET}' && \
       sudo find '${TARGET}' -name '._*' -delete"
  else
    create_working_tree_archive "${ARCHIVE}"
    "${SCP_CMD[@]}" -q "${ARCHIVE}" "${USER}@${HOST}:/tmp/instantlink-bridge-deploy.tar.gz"
    "${SSH_CMD[@]}" -t "${USER}@${HOST}" \
      "sudo mkdir -p '${TARGET}' && \
       sudo tar -xzf /tmp/instantlink-bridge-deploy.tar.gz -C '${TARGET}' --owner='${OWNER}' --group='${GROUP}' && \
       rm /tmp/instantlink-bridge-deploy.tar.gz && \
       sudo rm -rf '${TARGET}/.omc' '${TARGET}/target' && \
       sudo find '${TARGET}' -name '._*' -delete"
  fi
}

install_instantlink_artifacts_on_pi() {
  local artifacts_dir="$1"
  local lib_source="${artifacts_dir}/libinstantlink_ffi.so"
  local cli_source="${artifacts_dir}/instantlink"
  local workspace_root="${ROOT}/.."
  local artifact_manifest
  local remote_lib="/tmp/libinstantlink_ffi.so"
  local remote_cli="/tmp/instantlink"
  local remote_manifest="/tmp/instantlink-bridge-artifacts-manifest.json"

  if [[ ! -f "${lib_source}" ]]; then
    echo "ERROR: missing InstantLink FFI artifact: ${lib_source}" >&2
    echo "Run scripts/build-instantlink-artifacts.sh first or set INSTANTLINK_BRIDGE_INSTANTLINK_ARTIFACT_DIR." >&2
    exit 1
  fi
  if [[ ! -f "${cli_source}" ]]; then
    echo "ERROR: missing InstantLink CLI artifact: ${cli_source}" >&2
    echo "Run scripts/build-instantlink-artifacts.sh first or set INSTANTLINK_BRIDGE_INSTANTLINK_ARTIFACT_DIR." >&2
    exit 1
  fi

  artifact_manifest="$(mktemp -t instantlink-bridge-artifacts.XXXXXX.json)"
  render_instantlink_artifacts_manifest \
    "${artifact_manifest}" \
    "${artifacts_dir}" \
    "${lib_source}" \
    "${cli_source}" \
    "${workspace_root}"

  "${SCP_CMD[@]}" -q "${lib_source}" "${USER}@${HOST}:${remote_lib}"
  "${SCP_CMD[@]}" -q "${cli_source}" "${USER}@${HOST}:${remote_cli}"
  "${SCP_CMD[@]}" -q "${artifact_manifest}" "${USER}@${HOST}:${remote_manifest}"
  "${SSH_CMD[@]}" -t "${USER}@${HOST}" \
    "sudo install -D -m 0755 -o '${OWNER}' -g '${GROUP}' '${remote_lib}' '${TARGET}/lib/libinstantlink_ffi.so' && \
     sudo install -D -m 0755 -o '${OWNER}' -g '${GROUP}' '${remote_cli}' '${TARGET}/bin/instantlink' && \
     sudo install -D -m 0644 -o '${OWNER}' -g '${GROUP}' '${remote_manifest}' '${INSTANTLINK_ARTIFACTS_MANIFEST}' && \
     rm -f '${remote_lib}' '${remote_cli}' '${remote_manifest}'"
  rm -f "${artifact_manifest}"
}

render_instantlink_artifacts_manifest() {
  local output_path="$1"
  local artifacts_dir="$2"
  local lib_source="$3"
  local cli_source="$4"
  local workspace_root="$5"
  local lib_sha
  local cli_sha
  local commit_sha=""
  local branch=""
  local dirty="unknown"

  lib_sha="$(sha256_file "${lib_source}")"
  cli_sha="$(sha256_file "${cli_source}")"
  if git -C "${workspace_root}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    commit_sha="$(git -C "${workspace_root}" rev-parse --verify HEAD)"
    branch="$(git_branch_name "${workspace_root}")"
    if git_worktree_dirty "${workspace_root}"; then
      dirty=true
    else
      dirty=false
    fi
  fi

  "${PYTHON_BIN}" - \
    "${output_path}" \
    "$(utc_now)" \
    "${artifacts_dir}" \
    "${lib_source}" \
    "${lib_sha}" \
    "${cli_source}" \
    "${cli_sha}" \
    "${commit_sha}" \
    "${branch}" \
    "${dirty}" <<'PY'
import json
import pathlib
import sys

(
    output_path,
    recorded_at,
    artifacts_dir,
    lib_source,
    lib_sha,
    cli_source,
    cli_sha,
    commit_sha,
    branch,
    dirty,
) = sys.argv[1:]

manifest = {
    "schema_version": 1,
    "recorded_at_utc": recorded_at,
    "artifacts_dir": artifacts_dir,
    "instantlink_workspace": {
        "commit_sha": commit_sha or None,
        "branch": branch or None,
        "dirty": None if dirty == "unknown" else dirty == "true",
    },
    "artifacts": {
        "libinstantlink_ffi.so": {
            "source_path": lib_source,
            "sha256": lib_sha or None,
        },
        "instantlink": {
            "source_path": cli_source,
            "sha256": cli_sha or None,
        },
    },
}

pathlib.Path(output_path).write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
}

install_deployment_manifest_on_pi() {
  local manifest="$1"
  local remote_manifest="/tmp/instantlink-bridge-deployment-manifest.json"

  "${SCP_CMD[@]}" -q "${manifest}" "${USER}@${HOST}:${remote_manifest}"
  "${SSH_CMD[@]}" -t "${USER}@${HOST}" \
    "sudo install -D -m 0644 -o '${OWNER}' -g '${GROUP}' '${remote_manifest}' '${DEPLOY_MANIFEST_PATH}' && \
     rm '${remote_manifest}'"
}

fix_remote_config_permissions() {
  "${SSH_CMD[@]}" -t "${USER}@${HOST}" \
    "if [ -d '${CONFIG_DIR}' ]; then \
       sudo chgrp '${GROUP}' '${CONFIG_DIR}' && \
       sudo chmod 2770 '${CONFIG_DIR}' && \
       if [ -f '${CONFIG_DIR}/config.toml' ]; then \
         sudo chgrp '${GROUP}' '${CONFIG_DIR}/config.toml' && \
         sudo chmod 0660 '${CONFIG_DIR}/config.toml'; \
       fi; \
       if [ -f '${CONFIG_DIR}/hotspot.ssid' ]; then \
         sudo chgrp '${GROUP}' '${CONFIG_DIR}/hotspot.ssid' && \
         sudo chmod 0644 '${CONFIG_DIR}/hotspot.ssid'; \
       fi; \
       if [ -f '${CONFIG_DIR}/hotspot.psk' ]; then \
         sudo chgrp '${GROUP}' '${CONFIG_DIR}/hotspot.psk' && \
         sudo chmod 0640 '${CONFIG_DIR}/hotspot.psk'; \
       fi; \
     fi"
}

install_runtime_deps_on_pi() {
  "${SSH_CMD[@]}" -t "${USER}@${HOST}" \
    "sudo env \
       INSTANTLINK_BRIDGE_TARGET='${TARGET}' \
       INSTANTLINK_BRIDGE_OWNER='${OWNER}' \
       INSTANTLINK_BRIDGE_DEPLOY_METADATA_DIR='${DEPLOY_METADATA_DIR}' \
       INSTANTLINK_BRIDGE_RUNTIME_PACKAGES_ARTIFACT='${RUNTIME_PACKAGES_ARTIFACT}' \
       INSTANTLINK_BRIDGE_RUNTIME_APT_PACKAGES_ARTIFACT='${RUNTIME_APT_PACKAGES_ARTIFACT}' \
       INSTANTLINK_BRIDGE_RUNTIME_DEPS_MANIFEST='${RUNTIME_DEPS_MANIFEST}' \
       PYTHON_BIN='${TARGET_PYTHON_BIN}' \
       '${TARGET}/scripts/install-runtime-deps.sh'"
}

verify_remote_runtime_deps_current() {
  "${SSH_CMD[@]}" "${USER}@${HOST}" \
    "python3 - '${TARGET}/${CONSTRAINTS_RELATIVE_PATH}' '${RUNTIME_DEPS_MANIFEST}' <<'PY'
import hashlib
import json
import pathlib
import sys

constraints_path = pathlib.Path(sys.argv[1])
manifest_path = pathlib.Path(sys.argv[2])

if not constraints_path.exists():
    raise SystemExit(f'ERROR: missing deployed constraints file: {constraints_path}')
if not manifest_path.exists():
    raise SystemExit(
        'ERROR: runtime dependency manifest is missing; run deploy-to-pi.sh --deps before --restart'
    )

constraints_sha = hashlib.sha256(constraints_path.read_bytes()).hexdigest()
manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
runtime_sha = manifest.get('constraints_sha256')
if runtime_sha != constraints_sha:
    raise SystemExit(
        'ERROR: runtime dependencies are stale for deployed constraints; '
        'run deploy-to-pi.sh --deps --restart'
    )
PY"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --restart)
        RESTART=1
        shift
        ;;
      --system)
        SYSTEM=1
        shift
        ;;
      --deps)
        INSTALL_DEPS=1
        shift
        ;;
      --instantlink-artifacts)
        INSTALL_INSTANTLINK_ARTIFACTS=1
        shift
        ;;
      --allow-dirty)
        ALLOW_DIRTY=1
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        echo "Unknown argument: $1" >&2
        usage >&2
        exit 2
        ;;
    esac
  done
}

main() {
  parse_args "$@"
  init_ssh_commands

  ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  ARCHIVE="$(mktemp -t instantlink-bridge-deploy.XXXXXX.tar.gz)"
  MANIFEST="$(mktemp -t instantlink-bridge-deployment-manifest.XXXXXX.json)"
  cleanup() {
    rm -f "${ARCHIVE}" "${MANIFEST}"
  }
  trap cleanup EXIT

  cd "${ROOT}"

  EXCLUDES=(
    --exclude .git
    --exclude .DS_Store
    --exclude .mypy_cache
    --exclude .omc
    --exclude .pytest_cache
    --exclude .ruff_cache
    --exclude .venv
    --exclude .deployment
    --exclude 'third-party/*/target'
    --exclude __pycache__
    --exclude '*/__pycache__'
    --exclude '._*'
  )

  if ! git -C "${ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "ERROR: deploy-to-pi requires a git working tree for commit provenance" >&2
    exit 1
  fi

  COMMIT_SHA="$(git -C "${ROOT}" rev-parse --verify HEAD)"
  BRANCH="$(git_branch_name "${ROOT}")"
  REMOTE_URL="$(git_remote_url "${ROOT}" "${BRANCH}")"
  DEPLOY_TIMESTAMP="$(utc_now)"
  DIRTY=false
  SOURCE_MODE=git-archive
  if git_worktree_dirty "${ROOT}"; then
    DIRTY=true
    SOURCE_MODE=working-tree
  fi

  if [[ "${DIRTY}" == "true" && "${ALLOW_DIRTY}" -ne 1 ]]; then
    echo "ERROR: refusing to deploy dirty working tree; commit changes or pass --allow-dirty" >&2
    exit 1
  fi

  require_deploy_target

  CONSTRAINTS_SOURCE="${ROOT}/${CONSTRAINTS_RELATIVE_PATH}"
  if [[ ! -f "${CONSTRAINTS_SOURCE}" ]]; then
    echo "ERROR: missing Python constraints file at ${CONSTRAINTS_SOURCE}" >&2
    exit 1
  fi

  APT_PACKAGES=()
  while IFS= read -r package; do
    [[ -n "${package}" ]] && APT_PACKAGES+=("${package}")
  done < <(load_runtime_apt_packages "${ROOT}/scripts/install-runtime-deps.sh")

  render_deployment_manifest \
    "${MANIFEST}" \
    "${COMMIT_SHA}" \
    "${DIRTY}" \
    "${BRANCH}" \
    "${REMOTE_URL}" \
    "${DEPLOY_TIMESTAMP}" \
    "${SOURCE_MODE}" \
    "${CONSTRAINTS_RELATIVE_PATH}" \
    "${TARGET}/${CONSTRAINTS_RELATIVE_PATH}" \
    "${ROOT}/pyproject.toml" \
    "${ROOT}/scripts/install-runtime-deps.sh" \
    "${ROOT}/scripts/provision-sd.sh" \
    "${RUNTIME_PACKAGES_ARTIFACT}" \
    "${RUNTIME_DEPS_MANIFEST}" \
    "${ROOT}" \
    "${RUNTIME_APT_PACKAGES_ARTIFACT}" \
    "${INSTANTLINK_ARTIFACTS_MANIFEST}"

  if [[ "${DIRTY}" == "true" ]]; then
    deploy_working_tree_to_pi
  else
    create_commit_archive "${ROOT}" "${COMMIT_SHA}" "${ARCHIVE}"
    deploy_archive_to_pi "${ARCHIVE}"
  fi

  install_deployment_manifest_on_pi "${MANIFEST}"
  fix_remote_config_permissions

  if [[ "${SYSTEM}" -eq 1 ]]; then
    "${SSH_CMD[@]}" -t "${USER}@${HOST}" "sudo '${TARGET}/scripts/provision-sd.sh' /"
  fi

  if [[ "${INSTALL_INSTANTLINK_ARTIFACTS}" -eq 1 ]]; then
    INSTANTLINK_ARTIFACT_DIR="${INSTANTLINK_BRIDGE_INSTANTLINK_ARTIFACT_DIR:-${ROOT}/../target/aarch64-unknown-linux-gnu/release}"
    install_instantlink_artifacts_on_pi "${INSTANTLINK_ARTIFACT_DIR}"
  fi

  if [[ "${INSTALL_DEPS}" -eq 1 ]]; then
    install_runtime_deps_on_pi
  elif [[ "${RESTART}" -eq 1 ]]; then
    verify_remote_runtime_deps_current
  fi

  if [[ "${RESTART}" -eq 1 ]]; then
    "${SSH_CMD[@]}" -t "${USER}@${HOST}" "sudo systemctl restart instantlink-bridge.service"
  fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
