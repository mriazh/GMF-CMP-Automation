"""Utility functions for CMP Automation."""

import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def extract_token_from_email_body(body: str, sender: str | None = None) -> str | None:
    """Extract OTP token from email body using multiple patterns.

    Args:
        body: Email body text
        sender: Optional sender email/name for validation

    Returns:
        The token string if found, None otherwise.

    Priority:
    1. Explicitly labeled tokens (Token:, OTP:, Code:, Verification Code:)
    2. 6-digit numeric codes (most common OTP format)
    3. 8-digit numeric codes
    4. Alphanumeric 6-10 chars

    If multiple possible tokens are found and none are explicitly labeled,
    returns None to avoid ambiguity (fail closed).
    """
    if not body or not body.strip():
        return None

    # First priority: explicitly labeled tokens
    # Collect all labeled matches to check for ambiguity
    labeled_candidates = []

    labeled_patterns = [
        r"token[:\s]+([A-Za-z0-9]{6,})",
        r"otp[:\s]+([A-Za-z0-9]{6,})",
        r"code[:\s]+([A-Za-z0-9]{6,})",
        r"verification\s*code[:\s]+([A-Za-z0-9]{6,})",
        r"verification[:\s]+([A-Za-z0-9]{6,})",
        r"one.time.password[:\s]+([A-Za-z0-9]{6,})",
        r"one.time.code[:\s]+([A-Za-z0-9]{6,})",
        r"security\s*code[:\s]+([A-Za-z0-9]{6,})",
        r"auth(?:entication)?\s*code[:\s]+([A-Za-z0-9]{6,})",
    ]

    for pattern in labeled_patterns:
        matches = re.findall(pattern, body, re.IGNORECASE)
        for match in matches:
            token = match[0] if isinstance(match, tuple) else match
            # Only accept tokens that contain at least one digit (OTP-like)
            if isinstance(token, str) and len(token) >= 6 and re.search(r"\d", token):
                labeled_candidates.append(token)

    # Deduplicate labeled candidates
    labeled_candidates = list(dict.fromkeys(labeled_candidates))

    # If exactly one labeled candidate, return it
    if len(labeled_candidates) == 1:
        return str(labeled_candidates[0])

    # If multiple labeled candidates, fail closed
    if len(labeled_candidates) > 1:
        logger.debug("Ambiguous labeled token candidates in email body: %s", labeled_candidates)
        return None

    # Second priority: common OTP formats without labels
    # Collect all candidates to check for ambiguity
    candidates = []

    # 6-digit numeric (most common OTP)
    matches = re.findall(r"\b(\d{6})\b", body)
    candidates.extend(matches)

    # 8-digit numeric
    matches = re.findall(r"\b(\d{8})\b", body)
    candidates.extend(matches)

    # Alphanumeric 6-10 chars - but be more restrictive to avoid matching words
    # Only match if it looks like a token (has digits, or is all uppercase with digits)
    matches = re.findall(r"\b([A-Z0-9]{6,10})\b", body, re.IGNORECASE)
    # Filter: must contain at least one digit to be considered a token-like string
    matches = [m for m in matches if re.search(r"\d", m)]
    candidates.extend(matches)

    # Deduplicate candidates
    candidates = list(dict.fromkeys(candidates))

    # Filter candidates - prefer numeric, exclude obvious non-tokens
    filtered = []
    for token in candidates:
        # Skip timestamps like 20240115, 120000
        if re.match(r"^(19|20)\d{6}$", token):
            continue
        # Skip very long numbers that look like IDs
        if token.isdigit() and len(token) > 8:
            continue
        filtered.append(token)

    # If exactly one candidate, return it
    if len(filtered) == 1:
        return str(filtered[0])

    # If multiple candidates, fail closed (ambiguity)
    if len(filtered) > 1:
        logger.debug("Ambiguous token candidates in email body: %s", filtered)
        return None

    return None


def generate_export_filename(timestamp: datetime, download_dir: Path) -> Path:
    """Generate the timestamped products export filename."""
    return download_dir / f"sim_export_{timestamp.strftime('%Y%m%d_%H%M%S')}.xlsx"
