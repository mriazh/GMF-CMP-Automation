"""Tests for configuration management."""

import tempfile
from pathlib import Path

import pytest

from cmp_automation.config import Config, load_config, notifications_configured, validate_paths
from cmp_automation.exceptions import ConfigurationError


class TestConfig:
    """Tests for Config class."""

    def test_load_valid_config(self, monkeypatch):
        """Test loading valid configuration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir) / "firefox_profile"
            download_dir = Path(tmpdir) / "downloads"
            profile_dir.mkdir()
            download_dir.mkdir()

            monkeypatch.setenv("CMP_USERNAME", "testuser")
            monkeypatch.setenv("CMP_PASSWORD", "testpass")
            monkeypatch.setenv("GMF_EMAIL", "test@example.com")
            monkeypatch.setenv("GMF_PASSWORD", "mailpass")
            monkeypatch.setenv("FIREFOX_PROFILE_DIR", str(profile_dir))
            monkeypatch.setenv("DOWNLOAD_DIR", str(download_dir))
            monkeypatch.setenv("OTP_TIMEOUT_SECONDS", "120")
            monkeypatch.setenv("OTP_POLL_INTERVAL_SECONDS", "5")
            monkeypatch.setenv("TIMEZONE", "Asia/Jakarta")

            config = Config()
            assert config.cmp_username == "testuser"
            assert config.cmp_password == "testpass"
            assert config.gmf_email == "test@example.com"
            assert config.gmf_password == "mailpass"
            assert config.firefox_profile_dir == profile_dir.resolve()
            assert config.download_dir == download_dir.resolve()
            assert config.otp_timeout_seconds == 120
            assert config.otp_poll_interval_seconds == 5
            assert config.timezone == "Asia/Jakarta"

    def test_missing_required_env_raises(self, monkeypatch, tmp_path):
        """Test that missing required env vars raise safe ConfigurationError."""
        # Clear all env vars and avoid picking up a real .env from the repo
        for key in [
            "CMP_USERNAME",
            "CMP_PASSWORD",
            "GMF_EMAIL",
            "GMF_PASSWORD",
            "FIREFOX_PROFILE_DIR",
            "DOWNLOAD_DIR",
        ]:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(ConfigurationError, match="Missing required environment variable"):
            load_config()

    def test_load_config_safe_error_message(self, monkeypatch, tmp_path):
        """Test that load_config error message does not expose values."""
        # Clear all env vars and avoid picking up a real .env from the repo
        for key in [
            "CMP_USERNAME",
            "CMP_PASSWORD",
            "GMF_EMAIL",
            "GMF_PASSWORD",
            "FIREFOX_PROFILE_DIR",
            "DOWNLOAD_DIR",
        ]:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.chdir(tmp_path)

        try:
            load_config()
            pytest.fail("Expected ConfigurationError")
        except ConfigurationError as e:
            message = str(e)
            # Should list variable names but never any values
            assert "CMP_USERNAME" in message or "cmp_username" in message
            assert "test" not in message
            assert "password" not in message.lower().replace("cmp_password", "").replace(
                "gmf_password", ""
            ).replace("corp_password", "")
            assert "secret" not in message.lower()

    def test_invalid_timezone_raises(self, monkeypatch):
        """Test that invalid timezone raises error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir) / "firefox_profile"
            download_dir = Path(tmpdir) / "downloads"
            profile_dir.mkdir()
            download_dir.mkdir()

            monkeypatch.setenv("CMP_USERNAME", "testuser")
            monkeypatch.setenv("CMP_PASSWORD", "testpass")
            monkeypatch.setenv("GMF_EMAIL", "test@example.com")
            monkeypatch.setenv("GMF_PASSWORD", "mailpass")
            monkeypatch.setenv("FIREFOX_PROFILE_DIR", str(profile_dir))
            monkeypatch.setenv("DOWNLOAD_DIR", str(download_dir))
            monkeypatch.setenv("TIMEZONE", "Invalid/Timezone")

            with pytest.raises(Exception):  # pydantic validation error
                Config()

    def test_otp_timeout_bounds(self, monkeypatch):
        """Test OTP timeout validation bounds."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir) / "firefox_profile"
            download_dir = Path(tmpdir) / "downloads"
            profile_dir.mkdir()
            download_dir.mkdir()

            monkeypatch.setenv("CMP_USERNAME", "testuser")
            monkeypatch.setenv("CMP_PASSWORD", "testpass")
            monkeypatch.setenv("GMF_EMAIL", "test@example.com")
            monkeypatch.setenv("GMF_PASSWORD", "mailpass")
            monkeypatch.setenv("FIREFOX_PROFILE_DIR", str(profile_dir))
            monkeypatch.setenv("DOWNLOAD_DIR", str(download_dir))
            monkeypatch.setenv("OTP_TIMEOUT_SECONDS", "5")  # Below minimum

            with pytest.raises(Exception):
                Config()

            monkeypatch.setenv("OTP_TIMEOUT_SECONDS", "700")  # Above maximum
            with pytest.raises(Exception):
                Config()

    def test_imap_defaults(self, monkeypatch):
        """Test that IMAP settings use sensible defaults."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir) / "firefox_profile"
            download_dir = Path(tmpdir) / "downloads"
            profile_dir.mkdir()
            download_dir.mkdir()

            monkeypatch.setenv("CMP_USERNAME", "testuser")
            monkeypatch.setenv("CMP_PASSWORD", "testpass")
            monkeypatch.setenv("CORP_EMAIL", "test@example.com")
            monkeypatch.setenv("CORP_PASSWORD", "mailpass")
            monkeypatch.delenv("GMF_IMAP_HOST", raising=False)
            monkeypatch.delenv("GMF_IMAP_PORT", raising=False)
            monkeypatch.setenv("CORP_IMAP_HOST", "mail.company.local")
            monkeypatch.setenv("CORP_IMAP_PORT", "993")
            monkeypatch.setenv("FIREFOX_PROFILE_DIR", str(profile_dir))
            monkeypatch.setenv("DOWNLOAD_DIR", str(download_dir))

            config = Config()
            assert config.gmf_imap_host == "mail.company.local"
            assert config.gmf_imap_port == 993

    def test_imap_overrides(self, monkeypatch):
        """Test that IMAP settings can be overridden via environment variables."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir) / "firefox_profile"
            download_dir = Path(tmpdir) / "downloads"
            profile_dir.mkdir()
            download_dir.mkdir()

            monkeypatch.setenv("CMP_USERNAME", "testuser")
            monkeypatch.setenv("CMP_PASSWORD", "testpass")
            monkeypatch.setenv("GMF_EMAIL", "test@example.com")
            monkeypatch.setenv("GMF_PASSWORD", "mailpass")
            monkeypatch.setenv("FIREFOX_PROFILE_DIR", str(profile_dir))
            monkeypatch.setenv("DOWNLOAD_DIR", str(download_dir))
            monkeypatch.setenv("GMF_IMAP_HOST", "imap.example.com")
            monkeypatch.setenv("GMF_IMAP_PORT", "1430")

            config = Config()
            assert config.gmf_imap_host == "imap.example.com"
            assert config.gmf_imap_port == 1430

    def test_imap_port_bounds(self, monkeypatch):
        """Test that an out-of-range IMAP port is rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir) / "firefox_profile"
            download_dir = Path(tmpdir) / "downloads"
            profile_dir.mkdir()
            download_dir.mkdir()

            monkeypatch.setenv("CMP_USERNAME", "testuser")
            monkeypatch.setenv("CMP_PASSWORD", "testpass")
            monkeypatch.setenv("GMF_EMAIL", "test@example.com")
            monkeypatch.setenv("GMF_PASSWORD", "mailpass")
            monkeypatch.setenv("FIREFOX_PROFILE_DIR", str(profile_dir))
            monkeypatch.setenv("DOWNLOAD_DIR", str(download_dir))
            monkeypatch.setenv("GMF_IMAP_PORT", "70000")

            with pytest.raises(Exception):
                Config()

    def test_otp_clock_skew_tolerance_defaults(self, monkeypatch):
        """Test that OTP clock skew tolerance uses a sensible default."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir) / "firefox_profile"
            download_dir = Path(tmpdir) / "downloads"
            profile_dir.mkdir()
            download_dir.mkdir()

            monkeypatch.setenv("CMP_USERNAME", "testuser")
            monkeypatch.setenv("CMP_PASSWORD", "testpass")
            monkeypatch.setenv("GMF_EMAIL", "test@example.com")
            monkeypatch.setenv("GMF_PASSWORD", "mailpass")
            monkeypatch.setenv("FIREFOX_PROFILE_DIR", str(profile_dir))
            monkeypatch.setenv("DOWNLOAD_DIR", str(download_dir))

            config = Config()
            assert config.otp_clock_skew_tolerance_seconds == 120

    def test_otp_clock_skew_tolerance_override(self, monkeypatch):
        """Test that the tolerance can be overridden via environment variable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir) / "firefox_profile"
            download_dir = Path(tmpdir) / "downloads"
            profile_dir.mkdir()
            download_dir.mkdir()

            monkeypatch.setenv("CMP_USERNAME", "testuser")
            monkeypatch.setenv("CMP_PASSWORD", "testpass")
            monkeypatch.setenv("GMF_EMAIL", "test@example.com")
            monkeypatch.setenv("GMF_PASSWORD", "mailpass")
            monkeypatch.setenv("FIREFOX_PROFILE_DIR", str(profile_dir))
            monkeypatch.setenv("DOWNLOAD_DIR", str(download_dir))
            monkeypatch.setenv("OTP_CLOCK_SKEW_TOLERANCE_SECONDS", "60")

            config = Config()
            assert config.otp_clock_skew_tolerance_seconds == 60

    def test_otp_clock_skew_tolerance_bounds(self, monkeypatch):
        """Test that an out-of-range clock skew tolerance is rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir) / "firefox_profile"
            download_dir = Path(tmpdir) / "downloads"
            profile_dir.mkdir()
            download_dir.mkdir()

            monkeypatch.setenv("CMP_USERNAME", "testuser")
            monkeypatch.setenv("CMP_PASSWORD", "testpass")
            monkeypatch.setenv("GMF_EMAIL", "test@example.com")
            monkeypatch.setenv("GMF_PASSWORD", "mailpass")
            monkeypatch.setenv("FIREFOX_PROFILE_DIR", str(profile_dir))
            monkeypatch.setenv("DOWNLOAD_DIR", str(download_dir))
            monkeypatch.setenv("OTP_CLOCK_SKEW_TOLERANCE_SECONDS", "-1")

            with pytest.raises(Exception):
                Config()

            monkeypatch.setenv("OTP_CLOCK_SKEW_TOLERANCE_SECONDS", "700")
            with pytest.raises(Exception):
                Config()

    def test_path_expansion(self, monkeypatch):
        """Test that paths are expanded and resolved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir) / "firefox_profile"
            download_dir = Path(tmpdir) / "downloads"
            profile_dir.mkdir()
            download_dir.mkdir()

            monkeypatch.setenv("CMP_USERNAME", "testuser")
            monkeypatch.setenv("CMP_PASSWORD", "testpass")
            monkeypatch.setenv("GMF_EMAIL", "test@example.com")
            monkeypatch.setenv("GMF_PASSWORD", "mailpass")
            monkeypatch.setenv("FIREFOX_PROFILE_DIR", str(profile_dir))
            monkeypatch.setenv("DOWNLOAD_DIR", str(download_dir))

            config = Config()
            assert config.firefox_profile_dir.is_absolute()
            assert config.download_dir.is_absolute()
            assert config.firefox_profile_dir == profile_dir.resolve()
            assert config.download_dir == download_dir.resolve()

    def test_cmp_proxy_server_default_none(self, monkeypatch):
        """Test that cmp_proxy_server defaults to None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir) / "firefox_profile"
            download_dir = Path(tmpdir) / "downloads"
            profile_dir.mkdir()
            download_dir.mkdir()

            monkeypatch.setenv("CMP_USERNAME", "testuser")
            monkeypatch.setenv("CMP_PASSWORD", "testpass")
            monkeypatch.setenv("GMF_EMAIL", "test@example.com")
            monkeypatch.setenv("GMF_PASSWORD", "mailpass")
            monkeypatch.setenv("FIREFOX_PROFILE_DIR", str(profile_dir))
            monkeypatch.setenv("DOWNLOAD_DIR", str(download_dir))

            config = Config()
            assert config.cmp_proxy_server is None

    def test_cmp_proxy_server_via_cmp_proxy_server(self, monkeypatch):
        """Test cmp_proxy_server via CMP_PROXY_SERVER env var."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir) / "firefox_profile"
            download_dir = Path(tmpdir) / "downloads"
            profile_dir.mkdir()
            download_dir.mkdir()

            monkeypatch.setenv("CMP_USERNAME", "testuser")
            monkeypatch.setenv("CMP_PASSWORD", "testpass")
            monkeypatch.setenv("GMF_EMAIL", "test@example.com")
            monkeypatch.setenv("GMF_PASSWORD", "mailpass")
            monkeypatch.setenv("FIREFOX_PROFILE_DIR", str(profile_dir))
            monkeypatch.setenv("DOWNLOAD_DIR", str(download_dir))
            monkeypatch.setenv("CMP_PROXY_SERVER", "socks5://127.0.0.1:40000")

            config = Config()
            assert config.cmp_proxy_server == "socks5://127.0.0.1:40000"

    def test_cmp_proxy_server_via_proxy_server(self, monkeypatch):
        """Test cmp_proxy_server via PROXY_SERVER env var (alias)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir) / "firefox_profile"
            download_dir = Path(tmpdir) / "downloads"
            profile_dir.mkdir()
            download_dir.mkdir()

            monkeypatch.setenv("CMP_USERNAME", "testuser")
            monkeypatch.setenv("CMP_PASSWORD", "testpass")
            monkeypatch.setenv("GMF_EMAIL", "test@example.com")
            monkeypatch.setenv("GMF_PASSWORD", "mailpass")
            monkeypatch.setenv("FIREFOX_PROFILE_DIR", str(profile_dir))
            monkeypatch.setenv("DOWNLOAD_DIR", str(download_dir))
            monkeypatch.setenv("PROXY_SERVER", "http://proxy:8080")

            config = Config()
            assert config.cmp_proxy_server == "http://proxy:8080"

    def test_cmp_proxy_server_via_warp_proxy_url(self, monkeypatch):
        """Test cmp_proxy_server via WARP_PROXY_URL env var (alias)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir) / "firefox_profile"
            download_dir = Path(tmpdir) / "downloads"
            profile_dir.mkdir()
            download_dir.mkdir()

            monkeypatch.setenv("CMP_USERNAME", "testuser")
            monkeypatch.setenv("CMP_PASSWORD", "testpass")
            monkeypatch.setenv("GMF_EMAIL", "test@example.com")
            monkeypatch.setenv("GMF_PASSWORD", "mailpass")
            monkeypatch.setenv("FIREFOX_PROFILE_DIR", str(profile_dir))
            monkeypatch.setenv("DOWNLOAD_DIR", str(download_dir))
            monkeypatch.setenv("WARP_PROXY_URL", "socks5://127.0.0.1:50000")

            config = Config()
            assert config.cmp_proxy_server == "socks5://127.0.0.1:50000"

    def test_excel_output_dir_default_and_alias(self, monkeypatch, tmp_path):
        """Test excel_output_dir defaults to output/reports and respects report_dir alias."""
        monkeypatch.chdir(tmp_path)
        profile_dir = tmp_path / "firefox_profile"
        download_dir = tmp_path / "downloads"
        profile_dir.mkdir()
        download_dir.mkdir()

        monkeypatch.setenv("CMP_USERNAME", "testuser")
        monkeypatch.setenv("CMP_PASSWORD", "testpass")
        monkeypatch.setenv("GMF_EMAIL", "test@example.com")
        monkeypatch.setenv("GMF_PASSWORD", "mailpass")
        monkeypatch.setenv("FIREFOX_PROFILE_DIR", str(profile_dir))
        monkeypatch.setenv("DOWNLOAD_DIR", str(download_dir))
        monkeypatch.delenv("EXCEL_OUTPUT_DIR", raising=False)
        monkeypatch.delenv("REPORT_DIR", raising=False)

        config = Config()
        assert config.excel_output_dir == (tmp_path / "output" / "reports").resolve()

        custom_report_dir = tmp_path / "custom_reports"
        monkeypatch.setenv("REPORT_DIR", str(custom_report_dir))
        config_alias = Config()
        assert config_alias.excel_output_dir == custom_report_dir.resolve()

    def test_warp_configuration_defaults(self, monkeypatch, tmp_path):
        """Test WARP defaults for mode, port, and auto-connect."""
        monkeypatch.chdir(tmp_path)
        profile_dir = tmp_path / "firefox_profile"
        download_dir = tmp_path / "downloads"
        profile_dir.mkdir()
        download_dir.mkdir()

        monkeypatch.setenv("CMP_USERNAME", "testuser")
        monkeypatch.setenv("CMP_PASSWORD", "testpass")
        monkeypatch.setenv("GMF_EMAIL", "test@example.com")
        monkeypatch.setenv("GMF_PASSWORD", "mailpass")
        monkeypatch.setenv("FIREFOX_PROFILE_DIR", str(profile_dir))
        monkeypatch.setenv("DOWNLOAD_DIR", str(download_dir))
        monkeypatch.delenv("WARP_MODE", raising=False)
        monkeypatch.delenv("WARP_PROXY_PORT", raising=False)
        monkeypatch.delenv("WARP_AUTO_CONNECT", raising=False)

        config = Config()
        assert config.warp_mode == "proxy"
        assert config.warp_proxy_port == 40000
        assert config.warp_auto_connect is True

    def test_warp_proxy_port_bounds(self, monkeypatch, tmp_path):
        """Test warp_proxy_port validation bounds."""
        profile_dir = tmp_path / "firefox_profile"
        download_dir = tmp_path / "downloads"
        profile_dir.mkdir()
        download_dir.mkdir()

        monkeypatch.setenv("CMP_USERNAME", "testuser")
        monkeypatch.setenv("CMP_PASSWORD", "testpass")
        monkeypatch.setenv("GMF_EMAIL", "test@example.com")
        monkeypatch.setenv("GMF_PASSWORD", "mailpass")
        monkeypatch.setenv("FIREFOX_PROFILE_DIR", str(profile_dir))
        monkeypatch.setenv("DOWNLOAD_DIR", str(download_dir))

        monkeypatch.setenv("WARP_PROXY_PORT", "1023")
        with pytest.raises(ConfigurationError):
            load_config()

        monkeypatch.setenv("WARP_PROXY_PORT", "65536")
        with pytest.raises(ConfigurationError):
            load_config()


class TestNotificationConfig:
    """Tests for optional WhatsApp/GOWA notification configuration."""

    @staticmethod
    def _set_base_env(monkeypatch, tmp_path):
        """Populate the minimum required environment for Config()."""
        monkeypatch.chdir(tmp_path)
        profile_dir = tmp_path / "firefox_profile"
        download_dir = tmp_path / "downloads"
        profile_dir.mkdir(exist_ok=True)
        download_dir.mkdir(exist_ok=True)
        monkeypatch.setenv("CMP_USERNAME", "testuser")
        monkeypatch.setenv("CMP_PASSWORD", "testpass")
        monkeypatch.setenv("GMF_EMAIL", "test@example.com")
        monkeypatch.setenv("GMF_PASSWORD", "mailpass")
        monkeypatch.setenv("FIREFOX_PROFILE_DIR", str(profile_dir))
        monkeypatch.setenv("DOWNLOAD_DIR", str(download_dir))

    def test_notifications_disabled_by_default(self, monkeypatch, tmp_path):
        """WhatsApp notifications are opt-in and have safe dummy defaults."""
        self._set_base_env(monkeypatch, tmp_path)
        for key in (
            "WHATSAPP_NOTIFICATIONS_ENABLED",
            "GOWA_BASE_URL",
            "GOWA_TARGET_JID",
            "GOWA_DEVICE_ID",
            "GOWA_TIMEOUT_SECONDS",
        ):
            monkeypatch.delenv(key, raising=False)

        config = Config()
        assert config.whatsapp_notifications_enabled is False
        assert config.gowa_base_url is None
        assert config.gowa_target_jid is None
        assert config.gowa_device_id is None
        assert config.gowa_timeout_seconds == 5.0
        assert notifications_configured(config) is False

    def test_notifications_configured_via_environment(self, monkeypatch, tmp_path):
        """All notification settings load from their documented env vars."""
        self._set_base_env(monkeypatch, tmp_path)
        monkeypatch.setenv("WHATSAPP_NOTIFICATIONS_ENABLED", "true")
        monkeypatch.setenv("GOWA_BASE_URL", "https://gowa.example.invalid")
        monkeypatch.setenv("GOWA_TARGET_JID", "6280000000000@s.whatsapp.net")
        monkeypatch.setenv("GOWA_DEVICE_ID", "dummy-device")
        monkeypatch.setenv("GOWA_TIMEOUT_SECONDS", "7")

        config = Config()
        assert config.whatsapp_notifications_enabled is True
        assert config.gowa_base_url == "https://gowa.example.invalid"
        assert config.gowa_target_jid == "6280000000000@s.whatsapp.net"
        assert config.gowa_device_id == "dummy-device"
        assert config.gowa_timeout_seconds == 7.0
        assert notifications_configured(config) is True

    def test_notifications_configured_requires_full_configuration(self, monkeypatch, tmp_path):
        """Enabled but incomplete configuration never reports as configured."""
        self._set_base_env(monkeypatch, tmp_path)
        monkeypatch.setenv("WHATSAPP_NOTIFICATIONS_ENABLED", "true")
        monkeypatch.delenv("GOWA_BASE_URL", raising=False)
        monkeypatch.setenv("GOWA_TARGET_JID", "6280000000000@s.whatsapp.net")

        config = Config()
        assert notifications_configured(config) is False

    def test_notifications_configured_requires_enabled_flag(self, monkeypatch, tmp_path):
        """Fully populated values stay inert while the flag is off."""
        self._set_base_env(monkeypatch, tmp_path)
        monkeypatch.delenv("WHATSAPP_NOTIFICATIONS_ENABLED", raising=False)
        monkeypatch.setenv("GOWA_BASE_URL", "https://gowa.example.invalid")
        monkeypatch.setenv("GOWA_TARGET_JID", "6280000000000@s.whatsapp.net")

        config = Config()
        assert notifications_configured(config) is False

    def test_gowa_timeout_bounds(self, monkeypatch, tmp_path):
        """The GOWA request timeout is required to stay short and positive."""
        self._set_base_env(monkeypatch, tmp_path)

        monkeypatch.setenv("GOWA_TIMEOUT_SECONDS", "0")
        with pytest.raises(ConfigurationError):
            load_config()

        monkeypatch.setenv("GOWA_TIMEOUT_SECONDS", "-3")
        with pytest.raises(ConfigurationError):
            load_config()

        monkeypatch.setenv("GOWA_TIMEOUT_SECONDS", "600")
        with pytest.raises(ConfigurationError):
            load_config()

    def test_gowa_base_url_trailing_slash_normalized(self, monkeypatch, tmp_path):
        """A trailing slash is removed so endpoint joining stays predictable."""
        self._set_base_env(monkeypatch, tmp_path)
        monkeypatch.setenv("GOWA_BASE_URL", "https://gowa.example.invalid/")

        config = Config()
        assert config.gowa_base_url == "https://gowa.example.invalid"

    def test_gowa_base_url_requires_http_scheme(self, monkeypatch, tmp_path):
        """A non-HTTP scheme is rejected instead of failing at send time."""
        self._set_base_env(monkeypatch, tmp_path)
        monkeypatch.setenv("GOWA_BASE_URL", "ftp://gowa.example.invalid")

        with pytest.raises(ConfigurationError):
            load_config()

    def test_blank_gowa_values_are_treated_as_missing(self, monkeypatch, tmp_path):
        """Blank strings normalize to None rather than empty endpoints."""
        self._set_base_env(monkeypatch, tmp_path)
        monkeypatch.setenv("WHATSAPP_NOTIFICATIONS_ENABLED", "true")
        monkeypatch.setenv("GOWA_BASE_URL", "   ")
        monkeypatch.setenv("GOWA_TARGET_JID", "  ")

        config = Config()
        assert config.gowa_base_url is None
        assert config.gowa_target_jid is None
        assert notifications_configured(config) is False


class TestValidatePaths:
    """Tests for validate_paths function."""

    def test_valid_paths_pass(self, monkeypatch):
        """Test that valid paths pass validation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir) / "firefox_profile"
            download_dir = Path(tmpdir) / "downloads"
            template_file = Path(tmpdir) / "template.xlsx"
            profile_dir.mkdir()
            download_dir.mkdir()
            template_file.touch()

            monkeypatch.setenv("CMP_USERNAME", "testuser")
            monkeypatch.setenv("CMP_PASSWORD", "testpass")
            monkeypatch.setenv("GMF_EMAIL", "test@example.com")
            monkeypatch.setenv("GMF_PASSWORD", "mailpass")
            monkeypatch.setenv("FIREFOX_PROFILE_DIR", str(profile_dir))
            monkeypatch.setenv("DOWNLOAD_DIR", str(download_dir))
            monkeypatch.setenv("EXCEL_TEMPLATE_PATH", str(template_file))
            monkeypatch.setenv("EXCEL_OUTPUT_DIR", str(tmpdir))

            config = Config()
            validate_paths(config)  # Should not raise

    def test_missing_template_raises(self, monkeypatch):
        """Test that missing template file raises error when no example exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir) / "firefox_profile"
            download_dir = Path(tmpdir) / "downloads"
            profile_dir.mkdir()
            download_dir.mkdir()

            monkeypatch.setenv("CMP_USERNAME", "testuser")
            monkeypatch.setenv("CMP_PASSWORD", "testpass")
            monkeypatch.setenv("GMF_EMAIL", "test@example.com")
            monkeypatch.setenv("GMF_PASSWORD", "mailpass")
            monkeypatch.setenv("FIREFOX_PROFILE_DIR", str(profile_dir))
            monkeypatch.setenv("DOWNLOAD_DIR", str(download_dir))
            monkeypatch.setenv("EXCEL_TEMPLATE_PATH", str(Path(tmpdir) / "missing_template.xlsx"))

            config = Config()
            with pytest.raises(ConfigurationError, match="template.*does not exist"):
                validate_paths(config)

    def test_template_falls_back_to_example_file(self, monkeypatch):
        """Test that validate_paths automatically falls back to .example.xlsx when main template is absent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir) / "firefox_profile"
            download_dir = Path(tmpdir) / "downloads"
            profile_dir.mkdir()
            download_dir.mkdir()

            example_file = Path(tmpdir) / "template.example.xlsx"
            example_file.touch()

            monkeypatch.setenv("CMP_USERNAME", "testuser")
            monkeypatch.setenv("CMP_PASSWORD", "testpass")
            monkeypatch.setenv("GMF_EMAIL", "test@example.com")
            monkeypatch.setenv("GMF_PASSWORD", "mailpass")
            monkeypatch.setenv("FIREFOX_PROFILE_DIR", str(profile_dir))
            monkeypatch.setenv("DOWNLOAD_DIR", str(download_dir))
            monkeypatch.setenv("EXCEL_TEMPLATE_PATH", str(Path(tmpdir) / "template.xlsx"))

            config = Config()
            validate_paths(config)
            assert config.excel_template_path == example_file

    def test_missing_profile_dir_raises(self, monkeypatch):
        """Test that missing profile directory raises error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            download_dir = Path(tmpdir) / "downloads"
            download_dir.mkdir()

            monkeypatch.setenv("CMP_USERNAME", "testuser")
            monkeypatch.setenv("CMP_PASSWORD", "testpass")
            monkeypatch.setenv("GMF_EMAIL", "test@example.com")
            monkeypatch.setenv("GMF_PASSWORD", "mailpass")
            monkeypatch.setenv("FIREFOX_PROFILE_DIR", str(Path(tmpdir) / "nonexistent"))
            monkeypatch.setenv("DOWNLOAD_DIR", str(download_dir))

            config = Config()
            with pytest.raises(ConfigurationError, match="does not exist"):
                validate_paths(config)

    def test_missing_download_dir_raises(self, monkeypatch):
        """Test that missing download directory raises error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir) / "firefox_profile"
            profile_dir.mkdir()

            monkeypatch.setenv("CMP_USERNAME", "testuser")
            monkeypatch.setenv("CMP_PASSWORD", "testpass")
            monkeypatch.setenv("GMF_EMAIL", "test@example.com")
            monkeypatch.setenv("GMF_PASSWORD", "mailpass")
            monkeypatch.setenv("FIREFOX_PROFILE_DIR", str(profile_dir))
            monkeypatch.setenv("DOWNLOAD_DIR", str(Path(tmpdir) / "nonexistent"))

            config = Config()
            with pytest.raises(ConfigurationError, match="does not exist"):
                validate_paths(config)

    def test_profile_not_dir_raises(self, monkeypatch):
        """Test that profile path being a file raises error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_file = Path(tmpdir) / "firefox_profile"
            profile_file.write_text("not a dir")
            download_dir = Path(tmpdir) / "downloads"
            download_dir.mkdir()

            monkeypatch.setenv("CMP_USERNAME", "testuser")
            monkeypatch.setenv("CMP_PASSWORD", "testpass")
            monkeypatch.setenv("GMF_EMAIL", "test@example.com")
            monkeypatch.setenv("GMF_PASSWORD", "mailpass")
            monkeypatch.setenv("FIREFOX_PROFILE_DIR", str(profile_file))
            monkeypatch.setenv("DOWNLOAD_DIR", str(download_dir))

            config = Config()
            with pytest.raises(ConfigurationError, match="not a directory"):
                validate_paths(config)

    def test_validate_paths_creates_excel_output_dir(self, monkeypatch, tmp_path):
        """Test that validate_paths creates nested excel_output_dir (output/reports)."""
        profile_dir = tmp_path / "firefox_profile"
        download_dir = tmp_path / "downloads"
        template_file = tmp_path / "template.xlsx"
        nested_output_dir = tmp_path / "output" / "reports"
        profile_dir.mkdir()
        download_dir.mkdir()
        template_file.touch()

        monkeypatch.setenv("CMP_USERNAME", "testuser")
        monkeypatch.setenv("CMP_PASSWORD", "testpass")
        monkeypatch.setenv("GMF_EMAIL", "test@example.com")
        monkeypatch.setenv("GMF_PASSWORD", "mailpass")
        monkeypatch.setenv("FIREFOX_PROFILE_DIR", str(profile_dir))
        monkeypatch.setenv("DOWNLOAD_DIR", str(download_dir))
        monkeypatch.setenv("EXCEL_TEMPLATE_PATH", str(template_file))
        monkeypatch.setenv("EXCEL_OUTPUT_DIR", str(nested_output_dir))

        assert not nested_output_dir.exists()
        config = Config()
        validate_paths(config)
        assert nested_output_dir.exists()
        assert nested_output_dir.is_dir()
