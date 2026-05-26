from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy-to-pi.sh"
INSTALL_RUNTIME_DEPS_SCRIPT = REPO_ROOT / "scripts" / "install-runtime-deps.sh"
WIFI_MODE_SCRIPT = REPO_ROOT / "scripts" / "wifi-mode.sh"


def run(
    args: list[str],
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env is not None:
        merged_env.update(env)
    return subprocess.run(
        args,
        cwd=cwd,
        env=merged_env,
        text=True,
        capture_output=True,
        check=False,
    )


def git(repo: Path, *args: str) -> None:
    result = run(["git", *args], repo)
    assert result.returncode == 0, result.stderr


def test_deploy_refuses_dirty_worktree_before_ssh(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(DEPLOY_SCRIPT, scripts / "deploy-to-pi.sh")
    (repo / "README.md").write_text("clean\n", encoding="utf-8")

    git(repo, "init")
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=InstantLink Bridge Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "initial",
    )
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")

    result = run(["bash", "scripts/deploy-to-pi.sh"], repo)

    assert result.returncode == 1
    assert "refusing to deploy dirty working tree" in result.stderr


def test_render_deployment_manifest_records_provenance_and_dependency_hashes(
    tmp_path: Path,
) -> None:
    constraints = tmp_path / "constraints.txt"
    constraints.write_text("aiohttp==3.13.5\n", encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[project]\nname = 'InstantLink Bridge'\n", encoding="utf-8")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    install_script = scripts_dir / "install-runtime-deps.sh"
    install_script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    provision_script = scripts_dir / "provision-sd.sh"
    provision_script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    boot_config = config_dir / "boot-firmware-config.append"
    boot_config.write_text("dtoverlay=dwc2\n", encoding="utf-8")
    manifest = tmp_path / "deployment-manifest.json"
    invoke = tmp_path / "render.sh"
    invoke.write_text(
        "\n".join(
            [
                "set -euo pipefail",
                'source "${DEPLOY_SCRIPT}"',
                "APT_PACKAGES=(bluez dnsmasq)",
                "render_deployment_manifest \\",
                '  "${MANIFEST}" \\',
                '  "abc123" \\',
                '  "false" \\',
                '  "main" \\',
                '  "https://example.invalid/InstantLink Bridge.git" \\',
                '  "2026-05-20T00:00:00Z" \\',
                '  "git-archive" \\',
                '  "${CONSTRAINTS}" \\',
                '  "/opt/InstantLinkBridge/requirements/constraints.txt" \\',
                '  "${PYPROJECT}" \\',
                '  "${INSTALL_SCRIPT}" \\',
                '  "${PROVISION_SCRIPT}" \\',
                '  "/opt/InstantLinkBridge/.deployment/runtime-installed-packages.txt" \\',
                '  "/opt/InstantLinkBridge/.deployment/runtime-deps-manifest.json" \\',
                '  "${REPO_ROOT}" \\',
                '  "/opt/InstantLinkBridge/.deployment/runtime-apt-packages.txt"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = run(
        ["bash", str(invoke)],
        REPO_ROOT,
        {
            "DEPLOY_SCRIPT": str(DEPLOY_SCRIPT),
            "MANIFEST": str(manifest),
            "CONSTRAINTS": str(constraints),
            "PYPROJECT": str(pyproject),
            "INSTALL_SCRIPT": str(install_script),
            "PROVISION_SCRIPT": str(provision_script),
            "REPO_ROOT": str(tmp_path),
        },
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["commit_sha"] == "abc123"
    assert data["dirty"] is False
    assert data["branch"] == "main"
    assert data["remote_url"] == "https://example.invalid/InstantLink Bridge.git"
    assert data["source_mode"] == "git-archive"
    assert (
        data["dependencies"]["python_constraints"]["sha256"]
        == hashlib.sha256(constraints.read_bytes()).hexdigest()
    )
    assert (
        data["dependencies"]["pyproject"]["sha256"]
        == hashlib.sha256(pyproject.read_bytes()).hexdigest()
    )
    assert data["dependencies"]["runtime_install"] == {
        "apt_packages_artifact": "/opt/InstantLinkBridge/.deployment/runtime-apt-packages.txt",
        "installed_packages_artifact": (
            "/opt/InstantLinkBridge/.deployment/runtime-installed-packages.txt"
        ),
        "runtime_deps_manifest": "/opt/InstantLinkBridge/.deployment/runtime-deps-manifest.json",
        "script": "scripts/install-runtime-deps.sh",
        "script_sha256": hashlib.sha256(install_script.read_bytes()).hexdigest(),
    }
    assert data["dependencies"]["provision"]["apt_packages"] == ["bluez", "dnsmasq"]
    assert data["dependencies"]["provision"]["system_fingerprints"] == [
        {
            "path": "config/boot-firmware-config.append",
            "sha256": hashlib.sha256(boot_config.read_bytes()).hexdigest(),
        },
        {
            "path": "scripts/install-runtime-deps.sh",
            "sha256": hashlib.sha256(install_script.read_bytes()).hexdigest(),
        },
        {
            "path": "scripts/provision-sd.sh",
            "sha256": hashlib.sha256(provision_script.read_bytes()).hexdigest(),
        },
    ]


def test_install_runtime_deps_exposes_offline_mode_helpers() -> None:
    result = run(
        [
            "bash",
            "-c",
            (
                'source "${INSTALL_RUNTIME_DEPS_SCRIPT}"; '
                'is_truthy "1"; '
                'is_truthy "true"; '
                '! is_truthy "0"; '
                '! is_truthy ""'
            ),
        ],
        REPO_ROOT,
        {"INSTALL_RUNTIME_DEPS_SCRIPT": str(INSTALL_RUNTIME_DEPS_SCRIPT)},
    )

    assert result.returncode == 0, result.stderr


def test_deploy_script_documents_offline_dependency_forwarding() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "INSTANTLINK_BRIDGE_OFFLINE_DEPS" in text
    assert "INSTANTLINK_BRIDGE_SEED_VENV" in text
    assert "INSTANTLINK_BRIDGE_OFFLINE='${OFFLINE_DEPS}'" in text


def test_deploy_preserves_runtime_artifacts_when_syncing_source_with_delete() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    rsync_delete_commands = re.findall(r"sudo rsync -a --delete[^\n]+", text)

    assert len(rsync_delete_commands) >= 2
    for command in rsync_delete_commands:
        assert "--exclude .venv" in command
        assert "--exclude .deployment" in command
        assert "--exclude lib" in command
        assert "--exclude bin" in command


def test_deploy_uses_noninteractive_ssh_for_remote_maintenance_commands() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert '"${SSH_CMD[@]}" -t' not in text
    assert '"${SSH_CMD[@]}" -T' in text


def test_wifi_mode_helper_does_not_log_wifi_secrets_through_nested_sudo() -> None:
    text = WIFI_MODE_SCRIPT.read_text(encoding="utf-8")

    assert "run_root()" in text
    assert "run_root nmcli connection modify" in text
    assert "sudo nmcli" not in text
    assert "sudo tee" not in text


def test_deploy_bootstraps_runtime_identity_before_copy_for_system_installs() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    main = text[text.index("main() {") :]

    assert "bootstrap_remote_runtime_identity()" in text
    assert main.index("bootstrap_remote_runtime_identity") < main.index("deploy_working_tree_to_pi")
    assert main.index("bootstrap_remote_runtime_identity") < main.index("deploy_archive_to_pi")
