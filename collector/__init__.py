"""Read-only Bilibili & Douyin creator-data collector.

A single CLI (`python -m collector ...`) over two platforms:

- Bilibili: plain HTTPS against creator-center + public APIs (httpx).
- Douyin:   headless Chromium via Playwright (login state + a-bogus signing
            live only in a real browser; per-video fan growth is DOM-only).

Outputs go under ``social/<account>/<platform>/{raw,processed}``; secrets stay
under ``social/_secrets/<account>/<platform>/`` and never enter chat or logs.
"""

__all__ = ["__version__"]

__version__ = "2.6.0"
