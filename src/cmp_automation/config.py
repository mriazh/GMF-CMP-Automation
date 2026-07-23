"""Configuration management for CMP Automation."""

from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from pydantic import Field, ValidationError, field_validator
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

    # GMF Mailbox Credentials (used for direct IMAP OTP retrieval)
    gmf_email: str = Field(..., description="GMF mailbox email address")
    gmf_password: str = Field(..., description="GMF mailbox password")

    # GMF IMAP Configuration
    gmf_imap_host: str = Field(
        default="mail.gmf-aeroasia.co.id", description="GMF IMAP server host"
    )
    gmf_imap_port: int = Field(
        default=993, ge=1, le=65535, description="GMF IMAP server port"
    )

    # Firefox Configuration
    firefox_profile_dir: Path = Field(..., description="Path to persistent Firefox profile directory")
    download_dir: Path = Field(..., description="Directory for downloaded files")

    # OTP Configuration
    otp_timeout_seconds: int = Field(default=120, ge=10, le=600, description="OTP polling timeout in seconds")
    otp_poll_interval_seconds: int = Field(default=5, ge=1, le=60, description="OTP polling interval in seconds")
    otp_clock_skew_tolerance_seconds: int = Field(
        default=120,
        ge=0,
        le=600,
        description="Allowed clock skew tolerance in seconds for OTP email timestamp comparison",
    )

    # Timezone
    timezone: str = Field(default="Asia/Jakarta", description="Timezone for timestamp handling")

    # URLs
    cmp_login_url: str = Field(
        default="https://ep.iotcc.telkomsel.com/cas/login?service=https%3A%2F%2Fep.iotcc.telkomsel.com%2Fcas%2Foauth2.0%2FcallbackAuthorize%3Fclient_id%3DenterprisePortal%26redirect_uri%3Dhttps%253A%252F%252Fep.iotcc.telkomsel.com%26response_type%3Dcode%26client_name%3DCasOAuthClient",
        description="CMP login URL",
    )
    cmp_products_url: str = Field(default="https://ep.iotcc.telkomsel.com/#!products", description="CMP Products page URL")
    cmp_dashboard_url: str = Field(default="https://ep.iotcc.telkomsel.com/#!dashboard", description="CMP Dashboard page URL")

    # OTP Email subject
    otp_email_subject: str = Field(default="CMP - YOUR TOKEN", description="Exact subject of OTP email")

    @field_validator("firefox_profile_dir", "download_dir", mode="before")
    @classmethod
    def expand_path(cls, v: str | Path) -> Path:
        """Expand user home directory and resolve path."""
        if isinstance(v, str):
            v = Path(v)
        return v.expanduser().resolve()

    @field_validator("cmp_login_url", "cmp_products_url", "cmp_dashboard_url")
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

    @field_validator("gmf_imap_host")
    @classmethod
    def validate_imap_host(cls, v: str) -> str:
        """IMAP host must not be empty."""
        if not v or not v.strip():
            raise ValueError("IMAP host must not be empty")
        return v.strip()

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
            raise ConfigurationError(
                f"Missing required environment variable(s): {names}"
            ) from e
        raise ConfigurationError("Invalid configuration values") from e


def validate_paths(config: Config) -> None:
    """Validate that required paths exist and are accessible."""
    if not config.firefox_profile_dir.exists():
        raise ConfigurationError(f"Firefox profile directory does not exist: {config.firefox_profile_dir}")
    if not config.firefox_profile_dir.is_dir():
        raise ConfigurationError(f"Firefox profile path is not a directory: {config.firefox_profile_dir}")

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
