"""Configuration management for CMP Automation."""

from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .exceptions import ConfigurationError


class Config(BaseSettings):
    """Application configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # CMP Portal Credentials
    cmp_username: str = Field(..., description="CMP Portal username")
    cmp_password: str = Field(..., description="CMP Portal password")

    # Corporate Mailbox Credentials (used for direct IMAP OTP retrieval)
    corp_email: str = Field(
        ...,
        description="Corporate email address for OTP retrieval",
        validation_alias=AliasChoices("corp_email", "gmf_email", "office_email"),
    )
    corp_password: str = Field(
        ...,
        description="Corporate mailbox password",
        validation_alias=AliasChoices("corp_password", "gmf_password", "office_password"),
    )

    # Corporate IMAP Configuration
    corp_imap_host: str = Field(
        default="mail.company.local",
        description="Corporate IMAP server host",
        validation_alias=AliasChoices("corp_imap_host", "gmf_imap_host"),
    )
    corp_imap_port: int = Field(
        default=993,
        ge=1,
        le=65535,
        description="Corporate IMAP server port",
        validation_alias=AliasChoices("corp_imap_port", "gmf_imap_port"),
    )

    # Firefox Configuration
    firefox_profile_dir: Path = Field(
        ..., description="Path to persistent Firefox profile directory"
    )
    download_dir: Path = Field(..., description="Browser staging directory for temporary downloads")
    raw_xlsx_dir: Path | None = Field(
        default=None, description="Directory for preserved raw portal XLSX files"
    )
    image_dir: Path | None = Field(
        default=None, description="Directory for dashboard screenshots"
    )

    # Legacy property getters for backward compatibility
    @property
    def gmf_email(self) -> str:
        return self.corp_email

    @property
    def gmf_password(self) -> str:
        return self.corp_password

    @property
    def gmf_imap_host(self) -> str:
        return self.corp_imap_host

    @property
    def gmf_imap_port(self) -> int:
        return self.corp_imap_port
    logs_dir: Path | None = Field(
        default=None, description="Directory for application log files"
    )

    # OTP Configuration
    otp_timeout_seconds: int = Field(
        default=120, ge=10, le=600, description="OTP polling timeout in seconds"
    )
    otp_poll_interval_seconds: int = Field(
        default=5, ge=1, le=60, description="OTP polling interval in seconds"
    )
    otp_clock_skew_tolerance_seconds: int = Field(
        default=120,
        ge=0,
        le=600,
        description="Allowed clock skew tolerance in seconds for OTP email timestamp comparison",
    )

    # Timezone
    timezone: str = Field(default="Asia/Jakarta", description="Timezone for timestamp handling")

    # Host connectivity (all mutations are opt-in)
    connectivity_enabled: bool = Field(
        default=False, description="Enable Check Point/IMAP/WARP connectivity orchestration"
    )
    connectivity_allow_connect: bool = Field(
        default=False, description="Allow the run to initiate unconnected VPN tunnels"
    )
    connectivity_allow_disconnect: bool = Field(
        default=False, description="Allow the run to disconnect tunnels it owns"
    )
    checkpoint_trac_path: Path = Field(
        default=Path(r"C:\Program Files (x86)\CheckPoint\Endpoint Connect\trac.exe"),
        description="Confirmed Check Point trac executable path",
    )
    checkpoint_site: str = Field(default="CORP-VPN", description="Confirmed Check Point site")
    checkpoint_gateway: str = Field(default="CORP-GATEWAY01", description="Confirmed Check Point gateway")
    warp_cli_path: Path = Field(
        default=Path(r"C:\Program Files\Cloudflare\Cloudflare WARP\warp-cli.exe"),
        description="Confirmed Cloudflare WARP CLI path",
    )
    warp_mode: str = Field(default="warp", description="Required WARP full-tunnel mode")
    warp_trace_url: str = Field(
        default="https://www.cloudflare.com/cdn-cgi/trace",
        description="Approved Cloudflare trace URL",
    )

    # Bounded monitoring (a single pass remains the default)
    monitor_interval_seconds: int = Field(
        default=0,
        ge=0,
        le=86400,
        description="Dashboard refresh interval; 0 disables monitoring",
    )
    monitor_max_cycles: int = Field(
        default=0,
        ge=0,
        le=1000,
        description="Maximum additional dashboard verification cycles",
    )

    # URLs
    cmp_login_url: str = Field(
        default="https://ep.iotcc.telkomsel.com/cas/login?service=https%3A%2F%2Fep.iotcc.telkomsel.com%2Fcas%2Foauth2.0%2FcallbackAuthorize%3Fclient_id%3DenterprisePortal%26redirect_uri%3Dhttps%253A%252F%252Fep.iotcc.telkomsel.com%26response_type%3Dcode%26client_name%3DCasOAuthClient",
        description="CMP login URL",
    )
    cmp_products_url: str = Field(
        default="https://ep.iotcc.telkomsel.com/#!products", description="CMP Products page URL"
    )
    cmp_global_reports_url: str = Field(
        default="https://ep.iotcc.telkomsel.com/#!globalReports",
        description="CMP Global Reports page URL",
    )
    cmp_reports_url: str = Field(
        default="https://ep.iotcc.telkomsel.com/#!reports",
        description="CMP Reports / Usage Query page URL",
    )
    cmp_dashboard_url: str = Field(
        default="https://ep.iotcc.telkomsel.com/#!dashboard", description="CMP Dashboard page URL"
    )

    # Excel Report Configuration
    excel_template_path: Path = Field(
        default=Path("config/Daily-Data-Usage-M2M.xlsx"),
        description="Path to monthly Excel report template",
    )
    excel_output_dir: Path = Field(
        default=Path("output"),
        description="Directory for generated monthly Excel reports",
    )

    # OTP Email subject
    otp_email_subject: str = Field(
        default="CMP - YOUR TOKEN", description="Exact subject of OTP email"
    )

    @field_validator(
        "firefox_profile_dir",
        "download_dir",
        "raw_xlsx_dir",
        "excel_template_path",
        "excel_output_dir",
        "image_dir",
        "logs_dir",
        mode="before",
    )
    @classmethod
    def expand_path(cls, v: str | Path | None) -> Path | None:
        """Expand user home directory and resolve path."""
        if v is None:
            return None
        if isinstance(v, str):
            v = Path(v)
        return v.expanduser().resolve()

    @field_validator(
        "cmp_login_url",
        "cmp_products_url",
        "cmp_global_reports_url",
        "cmp_reports_url",
        "cmp_dashboard_url",
    )
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Allow only the approved HTTPS CMP host."""
        parsed = urlparse(v)
        allowed_hosts = {"ep.iotcc.telkomsel.com"}
        if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
            raise ValueError("Configured URL must use HTTPS and an approved host")
        if parsed.port is not None or parsed.username or parsed.password:
            raise ValueError("Configured URL must not contain credentials or a port")
        return v

    @field_validator("corp_imap_host")
    @classmethod
    def validate_imap_host(cls, v: str) -> str:
        """IMAP host must not be empty."""
        if not v or not v.strip():
            raise ValueError("IMAP host must not be empty")
        return v.strip()

    @field_validator("checkpoint_trac_path", "warp_cli_path", mode="before")
    @classmethod
    def expand_connectivity_path(cls, v: str | Path) -> Path:
        return Path(v).expanduser()

    @field_validator("warp_trace_url")
    @classmethod
    def validate_warp_trace_url(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme != "https" or parsed.hostname not in {"cloudflare.com", "www.cloudflare.com"}:
            raise ValueError("WARP trace URL must use an approved HTTPS Cloudflare host")
        if parsed.username or parsed.password or parsed.port:
            raise ValueError("WARP trace URL must not contain credentials or a port")
        return v

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        """Validate timezone is a valid IANA timezone."""
        try:
            ZoneInfo(v)
        except Exception as e:
            raise ValueError(f"Invalid timezone: {v}") from e
        return v

    def get_timezone(self) -> ZoneInfo:
        """Get timezone as ZoneInfo object."""
        return ZoneInfo(self.timezone)


def load_config() -> Config:
    """Load and validate configuration from environment."""
    try:
        return Config()  # type: ignore[call-arg]  # pydantic-settings loads from env vars
    except ValidationError as e:
        missing = [err["loc"][0] for err in e.errors() if err["type"] == "missing"]
        if missing:
            names = ", ".join(sorted(str(name) for name in missing))
            raise ConfigurationError(f"Missing required environment variable(s): {names}") from e
        raise ConfigurationError("Invalid configuration values") from e


def validate_paths(config: Config) -> None:
    """Validate that required paths exist and are accessible."""
    if not config.firefox_profile_dir.exists():
        raise ConfigurationError(
            f"Firefox profile directory does not exist: {config.firefox_profile_dir}"
        )
    if not config.firefox_profile_dir.is_dir():
        raise ConfigurationError(
            f"Firefox profile path is not a directory: {config.firefox_profile_dir}"
        )

    if not config.download_dir.exists():
        raise ConfigurationError(f"Download directory does not exist: {config.download_dir}")
    if not config.download_dir.is_dir():
        raise ConfigurationError(f"Download path is not a directory: {config.download_dir}")
    try:
        probe = config.download_dir / ".cmp_automation_write_test"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as e:
        raise ConfigurationError("Download directory is not writable") from e

    artifact_dirs = [config.raw_xlsx_dir, config.image_dir, config.excel_output_dir, config.logs_dir]
    for artifact_dir in artifact_dirs:
        if artifact_dir is not None:
            artifact_dir.mkdir(parents=True, exist_ok=True)
            if not artifact_dir.is_dir():
                raise ConfigurationError(f"Artifact path is not a directory: {artifact_dir}")

    if not config.excel_template_path.exists():
        example_fallback = config.excel_template_path.with_name(
            config.excel_template_path.stem + ".example" + config.excel_template_path.suffix
        )
        if example_fallback.exists() and example_fallback.is_file():
            config.excel_template_path = example_fallback
        else:
            raise ConfigurationError(
                f"Excel template file does not exist: {config.excel_template_path}"
            )
    if not config.excel_template_path.is_file():
        raise ConfigurationError(f"Excel template path is not a file: {config.excel_template_path}")

    config.excel_output_dir.mkdir(parents=True, exist_ok=True)
    if not config.excel_output_dir.is_dir():
        raise ConfigurationError(f"Excel output path is not a directory: {config.excel_output_dir}")
    try:
        probe = config.excel_output_dir / ".cmp_automation_write_test"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as e:
        raise ConfigurationError("Excel output directory is not writable") from e
