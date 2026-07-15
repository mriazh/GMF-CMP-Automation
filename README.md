# CMP Automation

Production-ready automation tool for the Telkomsel CMP Portal.

## Features

- **Firefox-only** automation (Chromium blocked by firewall)
- **Persistent Firefox profile** for session reuse
- **OTP retrieval** from GMF Webmail with timestamp validation
- **Products export** to XLSX with sorting
- **Dashboard screenshot** capture and embedding
- **Excel report generation** with embedded dashboard image
- **Secure configuration** via environment variables
- **Comprehensive logging** without secrets
- Unit and mocked integration tests; live smoke tests require VPN/mailbox access

## Requirements

- Python 3.11+
- Firefox browser installed
- Playwright Firefox binary, installed exactly with `python -m playwright install firefox`

## Installation

```bash
# Clone and navigate to project
cd GMF-CMP-Automation

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install dependencies
pip install -e ".[dev]"

# Install Playwright Firefox
playwright install firefox
```

## Configuration

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
# Edit .env with your credentials and paths
```

Required environment variables:

| Variable | Description |
|----------|-------------|
| `CMP_USERNAME` | CMP Portal username |
| `CMP_PASSWORD` | CMP Portal password |
| `GMF_EMAIL` | GMF Webmail email |
| `GMF_PASSWORD` | GMF Webmail password |
| `FIREFOX_PROFILE_DIR` | Path to persistent Firefox profile |
| `DOWNLOAD_DIR` | Directory for downloads |
| `OTP_TIMEOUT_SECONDS` | OTP polling timeout (default: 120) |
| `OTP_POLL_INTERVAL_SECONDS` | OTP polling interval (default: 5) |
| `TIMEZONE` | IANA timezone (default: Asia/Jakarta) |

## Usage

```bash
# Normal run (headless)
python -m cmp_automation

# Headed mode (visible browser)
python -m cmp_automation --headed

# Dry run (validate config and browser only)
python -m cmp_automation --dry-run

# Override timeout
python -m cmp_automation --timeout 180

# Override directories
python -m cmp_automation --download-dir /path/to/downloads --profile-dir /path/to/firefox/profile
```

## Output

The workflow produces:
1. `sim_export_YYYYMMDD_HHMMSS.xlsx` - Products export
2. `sim_export_YYYYMMDD_HHMMSS_with_dashboard.xlsx` - Final report with embedded dashboard screenshot

## Testing

```bash
# Run all tests
python -m pytest

# Run with coverage report
python -m pytest --cov=cmp_automation --cov-report=term-missing

# Run a specific test file
python -m pytest tests/test_mailbox.py -v
```

## Linting and Type Checking

```bash
# Lint
python -m ruff check src/ tests/

# Type check (strict)
python -m mypy src/cmp_automation/
```

## Project Structure

```
src/cmp_automation/
├── __init__.py          # Package exports
├── cli.py               # CLI entry point
├── config.py            # Configuration management
├── exceptions.py        # Custom exceptions
├── browser.py           # Firefox browser management
├── cmp_login.py         # CMP login & OTP flow
├── mailbox.py           # GMF Webmail OTP retrieval
├── products.py          # Products export
├── dashboard.py         # Dashboard screenshot
├── excel_report.py      # Excel report generation
└── workflow.py          # Main workflow orchestration

tests/
├── test_config.py
├── test_exceptions.py
├── test_otp_timestamp.py
├── test_token_extraction.py
├── test_excel_report.py
├── test_mailbox.py
├── test_utils.py
└── test_workflow.py
```

## Security and live-run notes

- Credentials are read only from environment variables and never stored in source code.
- Tokens, passwords, cookies, email bodies, and session data are not logged.
- `.env`, Firefox profiles, screenshots, reports, downloads, and logs are ignored by Git.
- OTP timestamps are timezone-aware and compared against the recorded workflow start time.
- Live runs require access to the GMF network/VPN and a mailbox account matching `GMF_EMAIL`.
- Unit tests and mocked integration tests use sanitized data only; they do not perform live login.

## License

Internal use only - GMF AeroAsia