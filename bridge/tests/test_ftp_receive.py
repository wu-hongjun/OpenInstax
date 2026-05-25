from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from instantlink_bridge.camera.ftp import (
    FtpReceiveService,
    FtpServiceFailedError,
    ReceivedImage,
    ftp_home_dir_for_incoming,
    validate_runtime_ftp_config,
)
from instantlink_bridge.config import (
    FtpConfig,
    FtpReceiveMode,
    FtpSourceKind,
    ftp_config_source_decision,
    ftp_source_decision,
)
from instantlink_bridge.net.health import FtpActivity, FtpActivityTracker


@pytest.mark.parametrize(
    ("mode", "remote_ip", "active_peer_hosts", "allowed", "source"),
    [
        (FtpReceiveMode.AUTO, "192.168.7.10", (), False, FtpSourceKind.USB),
        (FtpReceiveMode.AUTO, "192.168.8.10", (), True, FtpSourceKind.HOTSPOT),
        (FtpReceiveMode.AUTO, "192.168.5.20", ("192.168.5.149",), True, FtpSourceKind.PEER),
        (FtpReceiveMode.AUTO, "192.168.5.20", ("192.168.6.149",), False, FtpSourceKind.PEER),
        (FtpReceiveMode.AUTO, "169.254.44.2", (), False, FtpSourceKind.LINK_LOCAL),
        (FtpReceiveMode.WIRED, "192.168.7.10", (), False, FtpSourceKind.USB),
        (FtpReceiveMode.WIRED, "192.168.8.10", (), False, FtpSourceKind.HOTSPOT),
        (FtpReceiveMode.WIRED, "192.168.5.20", ("192.168.5.149",), False, FtpSourceKind.PEER),
        (FtpReceiveMode.HOTSPOT, "192.168.8.10", (), True, FtpSourceKind.HOTSPOT),
        (FtpReceiveMode.HOTSPOT, "192.168.7.10", (), False, FtpSourceKind.USB),
        (
            FtpReceiveMode.HOTSPOT,
            "192.168.5.20",
            ("192.168.5.149",),
            False,
            FtpSourceKind.PEER,
        ),
        (FtpReceiveMode.PEER, "192.168.5.20", ("192.168.5.149",), True, FtpSourceKind.PEER),
        (FtpReceiveMode.PEER, "192.168.6.20", ("192.168.5.149",), False, FtpSourceKind.PEER),
        (FtpReceiveMode.PEER, "192.168.7.10", (), False, FtpSourceKind.USB),
        (FtpReceiveMode.PEER, "192.168.8.10", (), False, FtpSourceKind.HOTSPOT),
        (FtpReceiveMode.PEER, "169.254.44.2", (), False, FtpSourceKind.LINK_LOCAL),
    ],
)
def test_ftp_source_policy_matches_receive_mode(
    mode: FtpReceiveMode,
    remote_ip: str,
    active_peer_hosts: tuple[str, ...],
    *,
    allowed: bool,
    source: FtpSourceKind,
) -> None:
    decision = ftp_source_decision(
        mode,
        remote_ip,
        usb_host="192.168.7.1",
        hotspot_host="192.168.8.1",
        active_peer_hosts=active_peer_hosts,
    )

    assert decision.allowed is allowed
    assert decision.source is source


def test_ftp_source_policy_rejects_peer_without_active_peer_network() -> None:
    decision = ftp_source_decision(
        FtpReceiveMode.PEER,
        "192.168.5.20",
        usb_host="192.168.7.1",
        hotspot_host="192.168.8.1",
        active_peer_hosts=(),
    )

    assert not decision.allowed
    assert decision.source is FtpSourceKind.PEER
    assert decision.reason == "peer_source_outside_active_networks"


def test_ftp_config_source_policy_uses_preferred_wifi_host_as_peer_scope() -> None:
    config = FtpConfig(
        mode=FtpReceiveMode.PEER,
        preferred_wifi_host="192.168.5.149",
    )

    allowed = ftp_config_source_decision(config, "192.168.5.20")
    rejected = ftp_config_source_decision(config, "192.168.6.20")

    assert allowed.allowed
    assert allowed.source is FtpSourceKind.PEER
    assert not rejected.allowed
    assert rejected.reason == "peer_source_outside_active_networks"


@pytest.mark.asyncio
async def test_ftp_handoff_enqueues_without_blocking_thread(tmp_path: Path) -> None:
    queue: asyncio.Queue[ReceivedImage] = asyncio.Queue(maxsize=1)
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"jpg")
    service = FtpReceiveService(
        FtpConfig(incoming_dir=tmp_path),
        queue,
        asyncio.get_running_loop(),
    )

    service._handoff_received_image(image_path, "192.168.7.10")
    await asyncio.sleep(0)

    received = queue.get_nowait()
    assert received == ReceivedImage(path=image_path, remote_ip="192.168.7.10")


@pytest.mark.asyncio
async def test_ftp_handoff_drops_file_when_queue_is_full(tmp_path: Path) -> None:
    queue: asyncio.Queue[ReceivedImage] = asyncio.Queue(maxsize=1)
    queue.put_nowait(ReceivedImage(path=tmp_path / "queued.jpg", remote_ip="192.168.7.10"))
    image_path = tmp_path / "overflow.jpg"
    image_path.write_bytes(b"jpg")
    overflows: list[tuple[ReceivedImage, int, int]] = []
    service = FtpReceiveService(
        FtpConfig(incoming_dir=tmp_path),
        queue,
        asyncio.get_running_loop(),
        queue_overflow_callback=lambda received, depth, max_size: overflows.append(
            (received, depth, max_size)
        ),
    )

    service._handoff_received_image(image_path, "192.168.7.11")
    await asyncio.sleep(0)

    assert not image_path.exists()
    assert queue.qsize() == 1
    assert overflows == [
        (ReceivedImage(path=image_path, remote_ip="192.168.7.11"), 1, 1),
    ]


@pytest.mark.asyncio
async def test_ftp_handoff_still_drops_file_when_overflow_callback_fails(
    tmp_path: Path,
) -> None:
    queue: asyncio.Queue[ReceivedImage] = asyncio.Queue(maxsize=1)
    queue.put_nowait(ReceivedImage(path=tmp_path / "queued.jpg", remote_ip="192.168.7.10"))
    image_path = tmp_path / "overflow.jpg"
    image_path.write_bytes(b"jpg")

    def failing_callback(
        _received: ReceivedImage,
        _depth: int,
        _max_size: int,
    ) -> None:
        raise RuntimeError("ui unavailable")

    service = FtpReceiveService(
        FtpConfig(incoming_dir=tmp_path),
        queue,
        asyncio.get_running_loop(),
        queue_overflow_callback=failing_callback,
    )

    service._handoff_received_image(image_path, "192.168.7.11")
    await asyncio.sleep(0)

    assert not image_path.exists()
    assert queue.qsize() == 1


@pytest.mark.asyncio
async def test_ftp_received_file_accepts_allowed_source(tmp_path: Path) -> None:
    queue: asyncio.Queue[ReceivedImage] = asyncio.Queue(maxsize=1)
    image_path = tmp_path / "peer.jpg"
    image_path.write_bytes(b"jpg")
    service = FtpReceiveService(
        FtpConfig(mode=FtpReceiveMode.PEER, incoming_dir=tmp_path),
        queue,
        asyncio.get_running_loop(),
        active_peer_host_provider=lambda: ["192.168.5.149"],
    )

    service._handle_received_file(str(image_path), "192.168.5.20")
    await asyncio.sleep(0)

    assert image_path.exists()
    assert queue.get_nowait() == ReceivedImage(path=image_path, remote_ip="192.168.5.20")


@pytest.mark.asyncio
async def test_ftp_received_file_relocates_root_upload_to_incoming(tmp_path: Path) -> None:
    queue: asyncio.Queue[ReceivedImage] = asyncio.Queue(maxsize=1)
    incoming_dir = tmp_path / "incoming"
    incoming_dir.mkdir()
    root_image_path = tmp_path / "root.jpg"
    root_image_path.write_bytes(b"jpg")
    service = FtpReceiveService(
        FtpConfig(mode=FtpReceiveMode.HOTSPOT, incoming_dir=incoming_dir),
        queue,
        asyncio.get_running_loop(),
    )

    service._handle_received_file(str(root_image_path), "192.168.8.59")
    await asyncio.sleep(0)

    relocated_path = incoming_dir / "root.jpg"
    assert not root_image_path.exists()
    assert relocated_path.exists()
    assert queue.get_nowait() == ReceivedImage(path=relocated_path, remote_ip="192.168.8.59")


@pytest.mark.asyncio
async def test_ftp_received_file_does_not_overwrite_queued_root_upload(tmp_path: Path) -> None:
    queue: asyncio.Queue[ReceivedImage] = asyncio.Queue(maxsize=2)
    incoming_dir = tmp_path / "incoming"
    incoming_dir.mkdir()
    existing_path = incoming_dir / "root.jpg"
    existing_path.write_bytes(b"first")
    root_image_path = tmp_path / "root.jpg"
    root_image_path.write_bytes(b"second")
    service = FtpReceiveService(
        FtpConfig(mode=FtpReceiveMode.HOTSPOT, incoming_dir=incoming_dir),
        queue,
        asyncio.get_running_loop(),
    )

    service._handle_received_file(str(root_image_path), "192.168.8.59")
    await asyncio.sleep(0)

    relocated_path = incoming_dir / "root-1.jpg"
    assert existing_path.read_bytes() == b"first"
    assert relocated_path.read_bytes() == b"second"
    assert queue.get_nowait() == ReceivedImage(path=relocated_path, remote_ip="192.168.8.59")


@pytest.mark.asyncio
async def test_ftp_received_file_records_upload_activity(tmp_path: Path) -> None:
    queue: asyncio.Queue[ReceivedImage] = asyncio.Queue(maxsize=1)
    image_path = tmp_path / "peer.jpg"
    image_path.write_bytes(b"jpg")
    tracker = FtpActivityTracker(clock=lambda: 1010.0)
    service = FtpReceiveService(
        FtpConfig(mode=FtpReceiveMode.PEER, incoming_dir=tmp_path),
        queue,
        asyncio.get_running_loop(),
        activity_tracker=tracker,
        active_peer_host_provider=lambda: ["192.168.5.149"],
    )

    service._handle_received_file(str(image_path), "192.168.5.20")
    await asyncio.sleep(0)

    assert tracker.snapshot() == FtpActivity(
        last_upload_at=1010.0,
        last_remote_ip="192.168.5.20",
    )


@pytest.mark.asyncio
async def test_ftp_pre_auth_connect_does_not_record_activity_until_login(
    tmp_path: Path,
) -> None:
    queue: asyncio.Queue[ReceivedImage] = asyncio.Queue(maxsize=1)
    tracker = FtpActivityTracker(clock=lambda: 1000.0)
    service = FtpReceiveService(
        FtpConfig(mode=FtpReceiveMode.PEER, incoming_dir=tmp_path),
        queue,
        asyncio.get_running_loop(),
        activity_tracker=tracker,
        active_peer_host_provider=lambda: ["192.168.5.149"],
    )

    assert service._accept_connection_source("192.168.5.20", event="ftp.connection_rejected")
    assert tracker.snapshot() == FtpActivity()

    assert service._record_authenticated_login("192.168.5.20")
    assert tracker.snapshot() == FtpActivity(
        last_connected_at=1000.0,
        last_remote_ip="192.168.5.20",
    )


@pytest.mark.asyncio
async def test_ftp_runtime_config_update_changes_source_gate(tmp_path: Path) -> None:
    queue: asyncio.Queue[ReceivedImage] = asyncio.Queue(maxsize=1)
    service = FtpReceiveService(
        FtpConfig(
            mode=FtpReceiveMode.WIRED,
            incoming_dir=tmp_path,
            password="12345678",
        ),
        queue,
        asyncio.get_running_loop(),
        active_peer_host_provider=lambda: ["192.168.5.149"],
    )

    wired_decision = service._source_decision("192.168.5.20")
    service.set_config(
        FtpConfig(
            mode=FtpReceiveMode.PEER,
            incoming_dir=tmp_path,
            password="12345678",
        )
    )
    peer_decision = service._source_decision("192.168.5.20")

    assert not wired_decision.allowed
    assert wired_decision.reason == "wired_mode_disabled_for_v1"
    assert peer_decision.allowed
    assert service.config.mode is FtpReceiveMode.PEER


@pytest.mark.asyncio
async def test_ftp_received_file_removes_rejected_usb_source_without_enqueue(
    tmp_path: Path,
) -> None:
    queue: asyncio.Queue[ReceivedImage] = asyncio.Queue(maxsize=1)
    image_path = tmp_path / "usb.jpg"
    image_path.write_bytes(b"jpg")
    service = FtpReceiveService(
        FtpConfig(mode=FtpReceiveMode.WIRED, incoming_dir=tmp_path),
        queue,
        asyncio.get_running_loop(),
    )

    service._handle_received_file(str(image_path), "192.168.7.10")
    await asyncio.sleep(0)

    assert not image_path.exists()
    assert queue.empty()


@pytest.mark.asyncio
async def test_ftp_raise_if_failed_reports_background_failure(tmp_path: Path) -> None:
    queue: asyncio.Queue[ReceivedImage] = asyncio.Queue(maxsize=1)
    service = FtpReceiveService(
        FtpConfig(incoming_dir=tmp_path),
        queue,
        asyncio.get_running_loop(),
    )
    service._started.set()
    service._record_failure(RuntimeError("server exited"))

    with pytest.raises(FtpServiceFailedError, match="FTP receive service stopped"):
        service.raise_if_failed()


def test_ftp_runtime_config_rejects_default_password(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="default password"):
        validate_runtime_ftp_config(FtpConfig(incoming_dir=tmp_path))


def test_ftp_runtime_config_accepts_provisioned_numeric_password(tmp_path: Path) -> None:
    validate_runtime_ftp_config(FtpConfig(incoming_dir=tmp_path, password="12345678"))


def test_ftp_home_exposes_incoming_as_camera_directory(tmp_path: Path) -> None:
    incoming_dir = tmp_path / "incoming"

    assert ftp_home_dir_for_incoming(incoming_dir) == tmp_path
