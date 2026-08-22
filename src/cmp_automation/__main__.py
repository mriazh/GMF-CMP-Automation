"""Module entry point for `python -m cmp_automation`."""

try:
    from .cli import cli_main
except ImportError:
    from cmp_automation.cli import cli_main

if __name__ == "__main__":
    cli_main()
