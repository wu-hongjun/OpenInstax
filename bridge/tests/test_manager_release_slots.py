from __future__ import annotations

import os
from pathlib import Path

import pytest

from instantlink_bridge.manager.release_slots import (
    CURRENT_LINK_NAME,
    PREVIOUS_LINK_NAME,
    ReleaseReference,
    ReleaseSlotPathError,
    RollbackState,
    UpdateStateStatus,
    apply_symlink_updates,
    ensure_release_slot_layout,
    plan_current_previous_switch,
    plan_rollback,
    read_release_link,
    read_rollback_state,
    release_symlink_target,
    write_rollback_state,
)


def test_current_previous_symlink_switch_plan_is_temp_dir_safe(tmp_path: Path) -> None:
    layout = ensure_release_slot_layout(tmp_path / "InstantLinkBridge")
    older_release = "2026-05-20T101500Z-v0.1.0"
    current_release = "2026-05-24T153000Z-v0.1.5"
    new_release = "2026-05-26T153000Z-v0.2.0"
    for release_id in (older_release, current_release, new_release):
        (layout.releases_dir / release_id).mkdir()
    layout.current_link.symlink_to(release_symlink_target(current_release))
    layout.previous_link.symlink_to(release_symlink_target(older_release))

    plan = plan_current_previous_switch(
        layout.root,
        new_release,
        backup_manifest_path=layout.root / "backups" / "update.manifest.json",
        now="2026-05-26T15:30:00Z",
    )

    assert plan.current_release_id == current_release
    assert plan.previous_release_id == older_release
    assert [(action.link_name, action.target) for action in plan.actions] == [
        (PREVIOUS_LINK_NAME, release_symlink_target(current_release)),
        (CURRENT_LINK_NAME, release_symlink_target(new_release)),
    ]
    assert plan.state.status is UpdateStateStatus.PENDING_VERIFICATION
    assert plan.state.active_release == new_release
    assert plan.state.previous_release == current_release

    apply_symlink_updates(plan.actions)

    current_link = read_release_link(layout.root, CURRENT_LINK_NAME)
    previous_link = read_release_link(layout.root, PREVIOUS_LINK_NAME)
    assert isinstance(current_link, ReleaseReference)
    assert isinstance(previous_link, ReleaseReference)
    assert os.readlink(layout.current_link) == release_symlink_target(new_release)
    assert os.readlink(layout.previous_link) == release_symlink_target(current_release)
    assert current_link.release_id == new_release
    assert previous_link.release_id == current_release


def test_release_slot_planning_rejects_traversal_release_ids(tmp_path: Path) -> None:
    layout = ensure_release_slot_layout(tmp_path / "InstantLinkBridge")
    (layout.releases_dir / "2026-05-24T153000Z-v0.1.5").mkdir()

    with pytest.raises(ReleaseSlotPathError):
        plan_current_previous_switch(layout.root, "../escape", require_release=False)


def test_rollback_state_round_trips_json(tmp_path: Path) -> None:
    state = RollbackState.pending_verification(
        active_release="2026-05-26T153000Z-v0.2.0",
        previous_release="2026-05-24T153000Z-v0.1.5",
        backup_manifest_path="/var/lib/InstantLinkBridge/backups/update.manifest.json",
        now="2026-05-26T15:30:00Z",
    )
    state_path = tmp_path / "update-state.json"

    write_rollback_state(state_path, state)
    restored = read_rollback_state(state_path)

    assert restored == state
    assert restored.to_dict()["status"] == "pending_verification"
    assert restored.mark_good(now="2026-05-26T15:31:00Z").status is UpdateStateStatus.GOOD


def test_rollback_plan_restores_previous_release_and_records_reason(tmp_path: Path) -> None:
    layout = ensure_release_slot_layout(tmp_path / "InstantLinkBridge")
    old_release = "2026-05-24T153000Z-v0.1.5"
    failed_release = "2026-05-26T153000Z-v0.2.0"
    for release_id in (old_release, failed_release):
        (layout.releases_dir / release_id).mkdir()
    layout.current_link.symlink_to(release_symlink_target(failed_release))
    layout.previous_link.symlink_to(release_symlink_target(old_release))
    state = RollbackState.pending_verification(
        active_release=failed_release,
        previous_release=old_release,
        backup_manifest_path="/var/lib/InstantLinkBridge/backups/update.manifest.json",
        now="2026-05-26T15:30:00Z",
    )

    plan = plan_rollback(
        layout.root,
        state=state,
        reason="health_check_failed",
        now="2026-05-26T15:31:00Z",
    )

    assert [(action.link_name, action.target) for action in plan.actions] == [
        (PREVIOUS_LINK_NAME, release_symlink_target(failed_release)),
        (CURRENT_LINK_NAME, release_symlink_target(old_release)),
    ]
    assert plan.state.status is UpdateStateStatus.ROLLED_BACK
    assert plan.state.active_release == old_release
    assert plan.state.previous_release == failed_release
    assert plan.state.reason == "health_check_failed"

    apply_symlink_updates(plan.actions)

    current_link = read_release_link(layout.root, CURRENT_LINK_NAME)
    previous_link = read_release_link(layout.root, PREVIOUS_LINK_NAME)
    assert isinstance(current_link, ReleaseReference)
    assert isinstance(previous_link, ReleaseReference)
    assert current_link.release_id == old_release
    assert previous_link.release_id == failed_release
