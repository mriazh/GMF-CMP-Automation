"""Opt-in live authentication test for the CMP Portal.

Skipped unless ``CMP_RUN_LIVE_TESTS=1`` is set - this test triggers a real
OTP email and performs a real login against the CMP portal, so it must never
run as part of the normal offline suite.

Explicit run (from the repository root, so the real ``.env`` is resolved):

.. code-block:: powershell

    $env:CMP_RUN_LIVE_TESTS="1"
    $env:CMP_LIVE_HEADED="1"   # optional; default is headless
    python -m pytest tests/test_live_login.py -m live -s -v
"""

import os
import tempfile
from pathlib import Path

import pytest

from cmp_automation.browser import BrowserManager
from cmp_automation.cmp_login import CMPLogin
from cmp_automation.config import load_config
from cmp_automation.mailbox import MailboxClient


@pytest.mark.live
async def test_live_login_to_products() -> None:
    """Exercise the real CAS + OTP authentication flow end to end.

    Uses a fresh temporary Firefox profile so an existing authenticated session
    cannot bypass CAS and OTP. The exact HTTPS host + exact Products URL is the
    live authentication guarantee; no speculative DOM selectors are required.
    The test never exports products, captures the dashboard, or generates Excel.
    """
    if os.environ.get("CMP_RUN_LIVE_TESTS") != "1":
        pytest.skip("Live tests disabled. Set CMP_RUN_LIVE_TESTS=1 to run.")

    headed = os.environ.get("CMP_LIVE_HEADED") == "1"

    config = load_config()
    mailbox = MailboxClient(config)
    try:
        await mailbox.connect()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Fresh profile + isolated downloads: never reuse the persistent
            # production profile (a stale authenticated session could bypass
            # CAS/OTP) and never touch the real download directory.
            live_config = config.model_copy(
                update={
                    "firefox_profile_dir": Path(tmpdir) / "firefox_profile",
                    "download_dir": Path(tmpdir) / "downloads",
                }
            )

            manager = BrowserManager(live_config, headed=headed)
            await manager.start()
            try:
                page = await manager.new_page()

                login = CMPLogin(live_config, mailbox)
                await login.login(page)

                # Final URL must be exactly the configured products page, which
                # covers both the CMP host and the exact #!products fragment.
                assert page.url == live_config.cmp_products_url, (
                    f"Unexpected final URL: {page.url!r}"
                )
            finally:
                # Close the browser BEFORE the temporary profile dir is removed,
                # otherwise Firefox still holds locks on the profile files and
                # temp-dir cleanup fails with PermissionError on Windows.
                await manager.cleanup()
    finally:
        await mailbox.disconnect()
