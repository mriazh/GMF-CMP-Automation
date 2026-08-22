#!/bin/bash
# Daily runner script for Telkomsel CMP Automation
# Calculates H-1 date, detects python environment, executes full pipeline
# Targets: /home/mriazh/Github-PC/GMF-CMP-Automation

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

# Execute the full pipeline with detailed logging
log "Executing: $PYTHON -m cmp_automation --mode full --date $YESTERDAY"
log "Starting pipeline execution..."

# Run with tee for detailed logging (both stdout and stderr)
$PYTHON -m cmp_automation --mode full --date "$YESTERDAY" 2>&1 | while IFS= read -r line; do
    log "$line"
done

EXIT_CODE=${PIPESTATUS[0]}

if [[ $EXIT_CODE -eq 0 ]]; then
    log "=== Pipeline completed successfully (exit code: $EXIT_CODE) ==="
else
    log "=== Pipeline FAILED (exit code: $EXIT_CODE) ==="
fi

log "=== Telkomsel CMP Automation Daily Runner Finished ==="
exit $EXIT_CODE
