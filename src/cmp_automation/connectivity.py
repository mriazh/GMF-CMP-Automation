"""Opt-in host connectivity adapters for the CMP workflow.

Use confirmed host facts without leaking secrets or owning existing tunnels.
Commands are executed with ``shell=False`` and subprocess output is only
retained long enough for state parsing. Existing tunnels are reused and only
tunnels established by this run may be disconnected with explicit permission.
"""

from __future__ import annotations

import asyncio
import re
import socket
import ssl
import subprocess
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from time import sleep
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .config import Config
from .exceptions import CMPAutomationError


class ConnectivityError(CMPAutomationError):
    """Raised when a configured connectivity prerequisite is not satisfied."""


@dataclass(frozen=True)
class CommandResult:
    """Sanitized subprocess result; output is retained only for parsing."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class CheckPointStatus:
    """Parsed Check Point state for one configured site."""

    site: str
    connected: bool


@dataclass(frozen=True)
class WarpStatus:
    """Parsed WARP state."""

    connected: bool
    mode: str | None


Runner = Callable[..., subprocess.CompletedProcess[str]]


DEFAULT_TRAC_PATH = Path(r"C:\Program Files (x86)\CheckPoint\Endpoint Connect\trac.exe")
DEFAULT_WARP_PATH = Path(r"C:\Program Files\Cloudflare\Cloudflare WARP\warp-cli.exe")
DEFAULT_WARP_TRACE_URL = "https://www.cloudflare.com/cdn-cgi/trace"


def _run_command(
    executable: Path,
    args: Sequence[str],
    timeout_seconds: float,
    runner: Runner,
) -> CommandResult:
    """Run a fixed executable with shell disabled and sanitized output boundary."""
    try:
        completed = runner(
            [str(executable), *args],
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ConnectivityError("Connectivity command timed out") from exc
    except OSError as exc:
        raise ConnectivityError("Connectivity command could not be started") from exc
    return CommandResult(completed.returncode, completed.stdout or "", completed.stderr or "")


def parse_checkpoint_status(output: str, site: str) -> CheckPointStatus:
    """Parse ``trac info`` without exposing output to callers or logs."""
    marker = f"Conn {site}:"
    start = output.find(marker)
    if start < 0:
        return CheckPointStatus(site, False)
    section = output[start:]
    status_match = re.search(r"(?im)^\s*status:\s*(\S+)", section)
    status = status_match.group(1).lower() if status_match else ""
    return CheckPointStatus(site, status in {"connected", "active"})


def parse_warp_status(output: str) -> WarpStatus:
    """Parse current WARP text status without retaining raw output."""
    normalized = output.lower()
    connected = "status update: connected" in normalized or "status: connected" in normalized
    mode: str | None = None
    for line in output.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("mode:"):
            mode = stripped.split(":", 1)[1].strip()
        if stripped.startswith("status update:"):
            state = stripped.split(":", 1)[1].strip()
            connected = state in {"connected", "connecting"}
    return WarpStatus(connected, mode)


class CheckPointClient:
    """Ownership-aware adapter for the confirmed Check Point client."""

    def __init__(
        self,
        config: Config,
        *,
        executable: Path | None = None,
        runner: Runner | None = None,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        self.config = config
        self.executable = executable or config.checkpoint_trac_path or DEFAULT_TRAC_PATH
        self._runner = runner or subprocess.run
        self._sleep = sleeper
        self.owned = False

    def command_info(self) -> list[str]:
        return [str(self.executable), "info", "-s", self.config.checkpoint_site]

    def command_connect(self) -> list[str]:
        return [
            str(self.executable),
            "connect",
            "-s",
            self.config.checkpoint_site,
            "-g",
            self.config.checkpoint_gateway,
        ]

    def command_disconnect(self) -> list[str]:
        return [str(self.executable), "disconnect", "-g", self.config.checkpoint_gateway]

    def status(self) -> CheckPointStatus:
        result = _run_command(self.executable, self.command_info()[1:], 30.0, self._runner)
        if result.returncode != 0:
            raise ConnectivityError("Check Point status failed")
        return parse_checkpoint_status(result.stdout, self.config.checkpoint_site)

    def ensure_connected(self, *, allow_connect: bool = False) -> None:
        current = self.status()
        if current.connected:
            return
        if not allow_connect:
            raise ConnectivityError("Check Point is not connected; explicit connect approval is required")
        result = _run_command(self.executable, self.command_connect()[1:], 60.0, self._runner)
        if result.returncode != 0:
            raise ConnectivityError("Check Point connect failed")
        self.owned = True
        if not self.status().connected:
            raise ConnectivityError("Check Point did not reach connected state")

    def disconnect(self, *, allow_disconnect: bool = False) -> None:
        if not self.owned or not allow_disconnect:
            return
        try:
            result = _run_command(self.executable, self.command_disconnect()[1:], 30.0, self._runner)
            if result.returncode != 0:
                raise ConnectivityError("Check Point disconnect failed")
        finally:
            self.owned = False


class WarpClient:
    """Ownership-aware adapter for the confirmed Cloudflare WARP client."""

    def __init__(
        self,
        config: Config,
        *,
        executable: Path | None = None,
        runner: Runner | None = None,
        opener: Callable[..., AbstractContextManager[object]] = urlopen,
    ) -> None:
        self.config = config
        self.executable = executable or config.warp_cli_path or DEFAULT_WARP_PATH
        self._runner = runner or subprocess.run
        self._opener = opener
        self.owned = False

    def command_status(self) -> list[str]:
        return [str(self.executable), "status"]

    def command_connect(self) -> list[str]:
        return [str(self.executable), "connect"]

    def command_disconnect(self) -> list[str]:
        return [str(self.executable), "disconnect"]

    def status(self) -> WarpStatus:
        result = _run_command(self.executable, self.command_status()[1:], 30.0, self._runner)
        if result.returncode != 0:
            raise ConnectivityError("WARP status failed")
        return parse_warp_status(result.stdout)

    def ensure_full_tunnel(self, *, allow_connect: bool = False) -> None:
        current = self.status()
        if not current.connected:
            if not allow_connect:
                raise ConnectivityError("WARP is not connected; explicit connect approval is required")
            result = _run_command(self.executable, self.command_connect()[1:], 60.0, self._runner)
            if result.returncode != 0:
                raise ConnectivityError("WARP connect failed")
            self.owned = True
            current = self.status()
        expected_mode = self.config.warp_mode.lower()
        if current.mode is not None and current.mode != expected_mode:
            raise ConnectivityError("WARP is not in the configured full-tunnel mode")

    def validate_trace(self) -> None:
        parsed = urlparse(self.config.warp_trace_url)
        if parsed.scheme != "https" or parsed.hostname not in {"cloudflare.com", "www.cloudflare.com"}:
            raise ConnectivityError("WARP trace URL is not approved")
        request = Request(self.config.warp_trace_url, method="GET")
        try:
            with self._opener(request, timeout=15.0) as response:
                read = getattr(response, "read", None)
                if not callable(read):
                    raise ConnectivityError("WARP trace response was invalid")
                raw_body = read()
                if not isinstance(raw_body, bytes):
                    raise ConnectivityError("WARP trace response was invalid")
                body = raw_body.decode("utf-8", errors="replace")
        except Exception as exc:
            raise ConnectivityError("WARP trace validation failed") from exc
        fields = {
            line.split("=", 1)[0].strip(): line.split("=", 1)[1].strip()
            for line in body.splitlines()
            if "=" in line
        }
        if fields.get("warp", "").lower() != "on":
            raise ConnectivityError("WARP trace did not confirm full tunnel")

    def disconnect(self, *, allow_disconnect: bool = False) -> None:
        if not self.owned or not allow_disconnect:
            return
        try:
            result = _run_command(self.executable, self.command_disconnect()[1:], 30.0, self._runner)
            if result.returncode != 0:
                raise ConnectivityError("WARP disconnect failed")
        finally:
            self.owned = False


class ConnectivityController:
    """Coordinate the PRD's Check Point -> IMAP -> WARP transition."""

    def __init__(
        self,
        config: Config,
        checkpoint: CheckPointClient | None = None,
        warp: WarpClient | None = None,
    ) -> None:
        self.config = config
        self.checkpoint = checkpoint or CheckPointClient(config)
        self.warp = warp or WarpClient(config)

    async def ensure_authentication_connectivity(self, *, allow_connect: bool = False) -> None:
        """Confirm IMAP DNS/TLS reachability, optionally after Check Point connect."""
        await asyncio.to_thread(self.checkpoint.ensure_connected, allow_connect=allow_connect)
        await asyncio.to_thread(self._probe_imap_tls)

    async def prepare_monitoring_connectivity(self, *, allow_connect: bool = False) -> None:
        """Optionally establish WARP and require a positive ``warp=on`` trace."""
        await asyncio.to_thread(self.warp.ensure_full_tunnel, allow_connect=allow_connect)
        await asyncio.to_thread(self.warp.validate_trace)

    async def release_authentication_connectivity(self, *, allow_disconnect: bool = False) -> None:
        await asyncio.to_thread(self.checkpoint.disconnect, allow_disconnect=allow_disconnect)

    async def cleanup(self, *, allow_disconnect: bool = False) -> None:
        await asyncio.to_thread(self.checkpoint.disconnect, allow_disconnect=allow_disconnect)
        await asyncio.to_thread(self.warp.disconnect, allow_disconnect=allow_disconnect)

    def _probe_imap_tls(self) -> None:
        host = self.config.gmf_imap_host
        port = self.config.gmf_imap_port
        try:
            addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            if not addresses:
                raise ConnectivityError("IMAP DNS resolution failed")
            with socket.create_connection((host, port), timeout=30.0) as raw_socket:
                context = ssl.create_default_context()
                with context.wrap_socket(raw_socket, server_hostname=host):
                    return
        except ConnectivityError:
            raise
        except (OSError, ssl.SSLError) as exc:
            raise ConnectivityError("IMAP TLS reachability failed") from exc
