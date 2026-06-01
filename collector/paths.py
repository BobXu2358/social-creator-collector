"""Workspace path layout and name validation.

All commands route through here so account/platform isolation is enforced in one
place: secrets under ``social/_secrets/<account>/<platform>/`` and outputs under
``social/<account>/<platform>/{raw,processed}``.
"""
from __future__ import annotations

import re
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")

PLATFORMS = ("bilibili", "douyin")

_NAME_RE = re.compile(r"[A-Za-z0-9_.-]+")


class CollectorError(Exception):
    """User-facing error; the CLI prints it as ``ERROR: ...`` and exits non-zero."""


def safe_name(value: str, *, kind: str = "name") -> str:
    """Reject anything that could escape its namespace.

    ``/`` is blocked by the charset, but ``.`` and ``..`` pass the regex (dot is
    allowed in names like ``a.b``) and ARE real path components — reject them
    explicitly so a bare ``..`` can't escape the workspace.
    """
    if not value or not _NAME_RE.fullmatch(value) or value in (".", ".."):
        raise CollectorError(
            f"{kind} must contain only letters, digits, '_', '.', '-' "
            f"and not be '.'/'..' (got {value!r})"
        )
    return value


def workspace_root(workspace: str | None) -> Path:
    return Path(workspace or Path.cwd()).expanduser().resolve()


def secret_dir(ws: Path, account: str, platform: str) -> Path:
    return ws / "social" / "_secrets" / safe_name(account, kind="account") / platform


def output_dirs(ws: Path, account: str, platform: str) -> tuple[Path, Path]:
    """Return ``(raw, processed)``, creating both."""
    base = ws / "social" / safe_name(account, kind="account") / platform
    raw, processed = base / "raw", base / "processed"
    raw.mkdir(parents=True, exist_ok=True)
    processed.mkdir(parents=True, exist_ok=True)
    return raw, processed
