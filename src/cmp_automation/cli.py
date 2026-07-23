"""CLI entry point for CMP Automation."""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .config import Config, load_config, validate_paths
from .exceptions import CMPAutomationError, ConfigurationError
from .workflow import run_workflow

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Telkomsel CMP Portal Automation Tool",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run browser in headed mode (visible)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration and browser launch without full workflow",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        help="Override OTP timeout in seconds",
    )
    parser.add_argument(
        "--download-dir",
        type=Path,
        help="Override download directory",
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        help="Override Firefox profile directory",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Set logging level",
    )
    return parser.parse_args()


def apply_overrides(config: Config, args: argparse.Namespace) -> None:
    """Apply CLI overrides and revalidate Pydantic constraints."""
    if args.timeout is not None:
        if not 10 <= args.timeout <= 600:
            raise ConfigurationError("--timeout must be between 10 and 600 seconds")
        config.otp_timeout_seconds = args.timeout
    if args.download_dir:
        config.download_dir = args.download_dir.expanduser().resolve()
    if args.profile_dir:
        config.firefox_profile_dir = args.profile_dir.expanduser().resolve()


async def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Set log level
    logging.getLogger().setLevel(args.log_level)

    try:
        # Load configuration
        logger.info("Loading configuration")
        config = load_config()

        # Apply CLI overrides
        apply_overrides(config, args)

        # Validate paths
        validate_paths(config)
        logger.info("Configuration validated successfully")

        # Run workflow
        result_path = await run_workflow(config, headed=args.headed, dry_run=args.dry_run)

        logger.info("Success! Output: %s", result_path)
        return 0

    except ConfigurationError as e:
        logger.error("Configuration error: %s", e)
        return 1
    except CMPAutomationError as e:
        logger.error("Automation error: %s", e)
        return 1
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 130
    except Exception:
        logger.exception("Unexpected error")
        return 1


def cli_main() -> None:
    """Synchronous entry point for setuptools.

    KeyboardInterrupt from ``asyncio.run(main())`` (e.g. Ctrl+C) is handled
    here because the ``except KeyboardInterrupt`` inside async ``main()``
    cannot intercept it once it propagates out of the event loop. It is logged
    and converted to exit code 130 without emitting a traceback. Ordinary
    unexpected exceptions are not suppressed and propagate normally.
    """
    try:
        code = asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        code = 130
    sys.exit(code)


if __name__ == "__main__":
    cli_main()
