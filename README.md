# Telkomsel CMP Automation

Production-ready Firefox automation for Telkomsel CMP Portal Daily Usage Query reporting.

## Features

- Firefox persistent-profile CAS login with direct IMAPS OTP retrieval.
- Strict Daily Usage Query SPA flow only: Reports → Usage Query → Daily → date range → Search → descending Total Data Usage → Export to xlsx → Download → Close.
- Portal raw XLSX filename is preserved unchanged, including its timestamp.
- Configured monthly workbook template populated by day tab (`01`–`31`).
- Same-day reruns replace only that day; other populated days remain intact.
- Dashboard sparks capture navigated through the visible Dashboard menu and anchored at `H15`.
- Optional, bounded Check Point/WARP readiness boundary; no implicit network mutation.
- Optional WhatsApp run notifications (START + one terminal SUCCESS/FAILED) via a GOWA gateway; disabled by default and always best-effort.

## Configuration

Copy `.env.example` to `.env` and set credentials, mailbox, and Firefox paths. The workbook paths default to the project layout:

```dotenv
EXCEL_TEMPLATE_PATH=config/Daily-Data-Usage-M2M.xlsx
EXCEL_OUTPUT_DIR=output/reports
```

The repository tracks a sanitized example template at `config/Daily-Data-Usage-M2M.example.xlsx`. Local working files in `config/` (such as `Daily-Data-Usage-M2M.xlsx`) are gitignored to prevent accidental exposure of production ICCIDs or locations. Set these optional `.env` values to keep artifacts out of Downloads (recommended):

```dotenv
RAW_XLSX_DIR=output/raw
IMAGE_DIR=output/images
LOGS_DIR=output/logs
```

The project template is `config/Daily-Data-Usage-M2M.xlsx`; generated artifacts use the structured output layout:

```text
output/
├─ reports/  # generated monthly Excel workbooks (Daily-Data-Usage-M2M-YYYYMM.xlsx)
├─ raw/      # preserved portal XLSX files
├─ images/   # timestamped dashboard captures (dashboard_YYYYMMDD_HHMMSS.png)
└─ logs/     # structured run logs (app.log)
```

## Usage

```bash
# 1. Full pipeline (default: Scrape + Generate)
python -m cmp_automation --headed --date 2026-09-14

# 2. Scrape only (Export raw XLSX + timestamped dashboard screenshot only)
python -m cmp_automation --mode scrape --headed --date 2026-09-14

# 3. Generate only (Populate monthly Excel from raw XLSX and image without browser)
python -m cmp_automation --mode generate --date 2026-09-14
python -m cmp_automation --mode generate --date 2026-09-14 --raw-xlsx output/raw/report_xxx.xlsx --image output/images/dashboard_xxx.png

# 4. Interactive Terminal Menu (Scrape, Generate, Full)
python -m cmp_automation --menu

# Finite date range (each date updates its own day tab)
python -m cmp_automation --start-date 2026-09-07 --end-date 2026-09-08 --headed

# Validate configuration and browser launch only
python -m cmp_automation --dry-run

## Artifact contract

Raw portal report example:

```text
report_20260907_125433_DAILY_USAGE_by_SIM.xlsx
```

Monthly workbook example:

```text
output/reports/Daily-Data-Usage-M2M-202609.xlsx
```

Dashboard images are saved under the configured image/download directory and embedded in the target day sheet at `H15`.

### Optional WhatsApp Notifications (GOWA)

Run lifecycle notifications can be delivered to a WhatsApp recipient through a
[GOWA](https://github.com/aldinokemih/gowaha) gateway. They are **disabled by
default** and only affect `full` and `scrape` runs; `generate` never notifies.

Enable them in the local (gitignored) `.env`:

```dotenv
WHATSAPP_NOTIFICATIONS_ENABLED=true
GOWA_BASE_URL=https://gowa-gateway.your-host.invalid
GOWA_TARGET_JID=0000000000000@s.whatsapp.net
# Optional: sent as the X-Device-Id header when you run multiple devices
# GOWA_DEVICE_ID=your-device
# Short per-request timeout in seconds (default 5)
GOWA_TIMEOUT_SECONDS=5
```

`.env.example` carries only placeholder values. The real gateway URL, device id,
and recipient JID stay local and must never be committed.

**Event flow for `full` / `scrape`:**

| Event | When |
| --- | --- |
| `START` | After config/argument validation, before any browser work |
| `SUCCESS` | Once, after all requested work including workbook generation |
| `FAILED` | Once, from the normalized configuration/automation error handlers |

A configuration error raised before `START` yields a single `FAILED` event. A
`KeyboardInterrupt` intentionally sends no terminal event, since an interrupted
run is neither a success nor a normalized failure.

**What a message contains:** application name, event, pipeline mode, target query
date or date range, elapsed duration and record count on success, and a sanitized
failure category (exception class name) on failure. Messages are deliberately
concise; no file path is ever included, for example:

```text
[GMF CMP Automation] SUCCESS | mode=full | date=2026-09-16 | elapsed=95s | records=34
```

**What a message never contains:** output file paths, ICCIDs, OTP values,
credentials, report rows or raw report data, screenshots, recipient JIDs, or raw
exception messages. Delivery is a single bounded HTTP
`POST {GOWA_BASE_URL}/send/message` with no retry and no queue.

**Best-effort guarantee:** notifications are wrapped defensively and can never
change the pipeline result or its exit code. A missing or partial configuration
is a silent no-op, and any send failure is logged as a warning without exposing
the message body, the recipient, or endpoint credentials.

### Auto-WARP SOCKS5 Proxy

When Cloudflare WARP is installed (`warp-cli`), the automation can automatically configure and manage an isolated SOCKS5 proxy on `127.0.0.1:40000` (`WARP_AUTO_CONNECT=true`, `WARP_MODE=proxy`) to route browser traffic without disrupting your system-wide VPN or network connectivity.

## Standalone Portable Build (Windows)

To build a standalone frozen portable executable and ZIP archive:

```powershell
# 1. Build standalone executable
.\scripts\build_exe.ps1

# 2. Package into release portable zip
.\scripts\package_portable.ps1 -Version "1.0.0"
```

The resulting zip archive will be generated in `release/Telkomsel-CMP-Automation-v1.0.0-portable.zip`.

## Automated Daily Scheduling (Debian / Linux Systemd)

To install the daily timer running automatically at 00:30 AM (processing $H-1$ date):

```bash
sudo bash systemd/install-timer.sh
```

## Verification

```bash
python -m pytest -q
python -m pytest --cov=cmp_automation --cov-report=term-missing -q
ruff check .
mypy src
python -m compileall -q src
git diff --check
```

Live authentication/portal tests are opt-in and require office-network or confirmed VPN/IMAP readiness.
