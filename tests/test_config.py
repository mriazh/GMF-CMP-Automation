"""Tests for configuration management."""

import tempfile
from pathlib import Path

import pytest

from cmp_automation.config import Config, load_config, validate_paths
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
        for key in ["CMP_USERNAME", "CMP_PASSWORD", "GMF_EMAIL", "GMF_PASSWORD",
                    "FIREFOX_PROFILE_DIR", "DOWNLOAD_DIR"]:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(ConfigurationError, match="Missing required environment variable"):
            load_config()

    def test_load_config_safe_error_message(self, monkeypatch, tmp_path):
        """Test that load_config error message does not expose values."""
        # Clear all env vars and avoid picking up a real .env from the repo
        for key in ["CMP_USERNAME", "CMP_PASSWORD", "GMF_EMAIL", "GMF_PASSWORD",
                    "FIREFOX_PROFILE_DIR", "DOWNLOAD_DIR"]:
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
            assert "password" not in message.lower().replace("cmp_password", "").replace("gmf_password", "")
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
            monkeypatch.setenv("GMF_EMAIL", "test@example.com")
            monkeypatch.setenv("GMF_PASSWORD", "mailpass")
            monkeypatch.setenv("FIREFOX_PROFILE_DIR", str(profile_dir))
            monkeypatch.setenv("DOWNLOAD_DIR", str(download_dir))

            config = Config()
            assert config.gmf_imap_host == "mail.gmf-aeroasia.co.id"
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


class TestValidatePaths:
    """Tests for validate_paths function."""

    def test_valid_paths_pass(self, monkeypatch):
        """Test that valid paths pass validation."""
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
            validate_paths(config)  # Should not raise

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
