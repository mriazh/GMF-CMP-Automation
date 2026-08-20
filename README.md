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

## Configuration

Copy `.env.example` to `.env` and set credentials, mailbox, and Firefox paths. The workbook paths default to the project layout:

```dotenv
EXCEL_TEMPLATE_PATH=config/Daily-Data-Usage-M2M.xlsx
EXCEL_OUTPUT_DIR=output
```

The repository tracks a sanitized example template at `config/Daily-Data-Usage-M2M.example.xlsx`. Local working files in `config/` (such as `Daily-Data-Usage-M2M.xlsx`) are gitignored to prevent accidental exposure of production ICCIDs or locations. Set these optional `.env` values to keep artifacts out of Downloads (recommended):

```dotenv
RAW_XLSX_DIR=output/raw
IMAGE_DIR=output/images
LOGS_DIR=output/logs
```

The project template is `config/Daily-Data-Usage-M2M.xlsx`; generated artifacts use the simple output layout:

```text
output/
├─ raw/      # preserved portal XLSX files
├─ images/   # timestamped dashboard captures (dashboard_YYYYMMDD_HHMMSS.png)
├─ logs/     # structured run logs (app.log)
└─ Daily-Data-Usage-M2M-YYYYMM.xlsx
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
output/Daily-Data-Usage-M2M-202609.xlsx
```

Dashboard images are saved under the configured image/download directory and embedded in the target day sheet at `H15`.

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
