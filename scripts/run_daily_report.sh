#!/bin/bash
# Daily runner script for Telkomsel CMP Automation
# Calculates H-1 date, detects python environment, executes full pipeline with automatic retry

set -euo pipefail

# Project directory
PROJECT_DIR="/home/mriazh/Github-PC/GMF-CMP-Automation"

# Logging function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

log "=== Telkomsel CMP Automation Daily Runner Started ==="
log "Project directory: $PROJECT_DIR"

# Change to project directory
cd "$PROJECT_DIR"

# Calculate yesterday's date (H-1) in YYYY-MM-DD format
YESTERDAY=$(date -d "yesterday" '+%Y-%m-%d')
log "Target date (H-1): $YESTERDAY"

# Detect python environment
if [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
    PYTHON="$PROJECT_DIR/.venv/bin/python"
    log "Using virtual environment python: $PYTHON"
elif command -v python3 &> /dev/null; then
    PYTHON="python3"
    log "Using system python3: $(which python3)"
else
    log "ERROR: No python3 found and .venv/bin/python not executable"
    exit 1
fi

MAX_ATTEMPTS=${MAX_ATTEMPTS:-3}
RETRY_DELAY=${RETRY_DELAY:-900}
EXIT_CODE=0

for ((attempt=1; attempt<=MAX_ATTEMPTS; attempt++)); do
    log "Attempt $attempt of $MAX_ATTEMPTS executing: $PYTHON -m cmp_automation --mode full --date $YESTERDAY"
    log "Starting pipeline execution..."

    # Run with tee/loop for detailed logging (both stdout and stderr)
    set +e
    $PYTHON -m cmp_automation --mode full --date "$YESTERDAY" 2>&1 | while IFS= read -r line; do
        log "$line"
    done
    EXIT_CODE=${PIPESTATUS[0]}
    set -e

    if [[ $EXIT_CODE -eq 0 ]]; then
        log "=== Pipeline completed successfully (exit code: $EXIT_CODE) ==="
        break
    else
        log "=== Pipeline FAILED (attempt $attempt of $MAX_ATTEMPTS, exit code: $EXIT_CODE) ==="
        if (( attempt < MAX_ATTEMPTS )); then
            log "Cleaning up stale browser locks..."
            $PYTHON -c "from pathlib import Path; from cmp_automation.browser import _clear_stale_firefox_lock; cfg_prof = Path('/home/mriazh/cmp_firefox_profile'); _clear_stale_firefox_lock(cfg_prof); loc_prof = Path('firefox_profile'); _clear_stale_firefox_lock(loc_prof)" || true
            log "Waiting ${RETRY_DELAY}s before retry..."
            sleep "$RETRY_DELAY"
        else
            log "=== Pipeline FAILED after $MAX_ATTEMPTS attempts ==="
        fi
    fi
done

log "=== Telkomsel CMP Automation Daily Runner Finished ==="
exit $EXIT_CODE
