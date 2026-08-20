"""Offline tests for the host connectivity adapters."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from cmp_automation.config import Config
from cmp_automation.connectivity import (
    CheckPointClient,
    ConnectivityController,
    ConnectivityError,
    WarpClient,
    parse_checkpoint_status,
    parse_warp_status,
)


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        cmp_username="user",
        cmp_password="pass",
        corp_email="mail@example.com",
        corp_password="pass",
        firefox_profile_dir=tmp_path / "profile",
        download_dir=tmp_path / "output",
        checkpoint_site="CORP-VPN",
        checkpoint_gateway="CORP-GATEWAY01",
    )


def completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["tool"], returncode, stdout, "")


def test_parse_checkpoint_connected_status() -> None:
    output = "Conn CORP-VPN:\n\tstatus: Connected\n\tgateway list:\n\t*(Connected) CORP-GATEWAY01"
    assert parse_checkpoint_status(output, "CORP-VPN").connected is True
    assert parse_checkpoint_status("Conn CORP-VPN:\n\tstatus: Disconnected", "CORP-VPN").connected is False


def test_parse_warp_status() -> None:
    assert parse_warp_status("Status update: Connected\nMode: Warp").connected is True
    status = parse_warp_status("Status update: Disconnected")
    assert status.connected is False


def test_checkpoint_commands_have_no_credentials(config: Config) -> None:
    client = CheckPointClient(config)
    assert client.command_connect() == [
        str(config.checkpoint_trac_path),
        "connect",
        "-s",
        "CORP-VPN",
        "-g",
        "CORP-GATEWAY01",
    ]
    assert all(value not in "user pass" for value in client.command_connect())


def test_preconnected_checkpoint_is_reused_without_connect(config: Config) -> None:
    calls: list[list[str]] = []

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return completed("Conn CORP-VPN:\n\tstatus: Connected\n")

    client = CheckPointClient(config, runner=runner)
    client.ensure_connected()
    assert len(calls) == 1
    assert calls[0][1:3] == ["info", "-s"]
    client.disconnect(allow_disconnect=True)
    assert len(calls) == 1


def test_checkpoint_requires_explicit_connect_approval(config: Config) -> None:
    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return completed("Conn CORP-VPN:\n\tstatus: Disconnected\n")

    with pytest.raises(ConnectivityError, match="explicit connect approval"):
        CheckPointClient(config, runner=runner).ensure_connected()


def test_warp_requires_explicit_connect_approval(config: Config) -> None:
    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return completed("Status update: Disconnected\n", returncode=0)

    with pytest.raises(ConnectivityError, match="explicit connect approval"):
        WarpClient(config, runner=runner).ensure_full_tunnel()


def test_warp_trace_requires_warp_on(config: Config) -> None:
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = b"warp=off\n"
    with pytest.raises(ConnectivityError, match="did not confirm full tunnel"):
        WarpClient(config, opener=Mock(return_value=response)).validate_trace()


@pytest.mark.asyncio
async def test_controller_runs_phases_without_mutating_tunnels(config: Config) -> None:
    checkpoint = Mock()
    checkpoint.ensure_connected = Mock()
    checkpoint.disconnect = Mock()
    warp = Mock()
    warp.ensure_full_tunnel = Mock()
    warp.validate_trace = Mock()
    warp.disconnect = Mock()
    controller = ConnectivityController(config, checkpoint=checkpoint, warp=warp)

    controller._probe_imap_tls = Mock()
    await controller.ensure_authentication_connectivity()
    await controller.prepare_monitoring_connectivity()
    await controller.cleanup()

    checkpoint.ensure_connected.assert_called_once_with(allow_connect=False)
    checkpoint.disconnect.assert_called_once_with(allow_disconnect=False)
    warp.ensure_full_tunnel.assert_called_once_with(allow_connect=False)
    warp.disconnect.assert_called_once_with(allow_disconnect=False)
