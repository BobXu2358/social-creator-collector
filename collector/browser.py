"""Cross-platform Chromium resolution for Playwright.

The old code hardcoded ``/usr/bin/chromium`` (Linux/CI only) and, in one script,
a machine-specific ms-playwright cache path. Both broke on a fresh machine. The
default here is Playwright's *bundled* Chromium — install it once with
``python -m playwright install chromium`` and it works on macOS/Linux/Windows
without knowing any path.

Override order (first match wins):
  1. ``--chromium /path/to/binary`` CLI flag
  2. ``SCC_CHROMIUM`` env var (an executable path)
  3. ``SCC_CHROMIUM_CHANNEL`` env var (e.g. ``chrome`` / ``msedge`` — use an
     installed browser instead of the bundled one)
  4. bundled Chromium (no path needed)
"""
from __future__ import annotations

import os
from typing import Any


def launch_kwargs(chromium: str | None = None) -> dict[str, Any]:
    """Return kwargs for ``playwright.chromium.launch(**kwargs)``.

    Always includes hardening args; adds ``executable_path`` or ``channel`` only
    when an override is supplied.
    """
    kwargs: dict[str, Any] = {
        "args": [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ],
    }
    explicit = chromium or os.environ.get("SCC_CHROMIUM")
    channel = os.environ.get("SCC_CHROMIUM_CHANNEL")
    if explicit:
        kwargs["executable_path"] = explicit
    elif channel:
        kwargs["channel"] = channel
    return kwargs


BUNDLED_HINT = (
    "No Chromium found. Install Playwright's bundled browser once:\n"
    "    python -m playwright install chromium\n"
    "or point --chromium / $SCC_CHROMIUM at an existing binary, "
    "or set $SCC_CHROMIUM_CHANNEL=chrome to use installed Chrome."
)
