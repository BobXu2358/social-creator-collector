from __future__ import annotations

import json
import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REQUIRED_CREDENTIAL_FIELDS = ["SESSDATA", "bili_jct", "buvid3"]


@dataclass(frozen=True)
class BilibiliCredentialCheck:
    account_profile: str
    credential_path: Path
    exists: bool
    missing_fields: list[str]
    usable: bool

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "account_profile": self.account_profile,
            "credential_path": str(self.credential_path),
            "credential_exists": self.exists,
            "missing_fields": self.missing_fields,
            "usable": self.usable,
        }


def credential_path(secret_root: Path, account_profile: str) -> Path:
    if not account_profile or "/" in account_profile or ".." in account_profile:
        raise ValueError("account_profile must be a simple profile name")
    return secret_root / f"{account_profile}.credentials.json"


def check_credentials(secret_root: Path, account_profile: str) -> BilibiliCredentialCheck:
    path = credential_path(secret_root, account_profile)
    if not path.exists():
        return BilibiliCredentialCheck(
            account_profile=account_profile,
            credential_path=path,
            exists=False,
            missing_fields=REQUIRED_CREDENTIAL_FIELDS.copy(),
            usable=False,
        )

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    missing = [field for field in REQUIRED_CREDENTIAL_FIELDS if not data.get(field)]
    return BilibiliCredentialCheck(
        account_profile=account_profile,
        credential_path=path,
        exists=True,
        missing_fields=missing,
        usable=not missing,
    )


def load_credential_data(secret_root: Path, account_profile: str) -> dict[str, str]:
    check = check_credentials(secret_root, account_profile)
    if not check.usable:
        raise ValueError(f"credential is not usable: {check.credential_path}")
    with check.credential_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return {field: str(data[field]) for field in REQUIRED_CREDENTIAL_FIELDS}


def create_credential(secret_root: Path, account_profile: str) -> Any:
    from bilibili_api import Credential  # type: ignore

    data = load_credential_data(secret_root, account_profile)
    return Credential(
        sessdata=data["SESSDATA"],
        bili_jct=data["bili_jct"],
        buvid3=data["buvid3"],
    )


async def _probe_self_info_async(secret_root: Path, account_profile: str) -> dict[str, Any]:
    from bilibili_api import user  # type: ignore

    credential = create_credential(secret_root, account_profile)
    data = await user.get_self_info(credential)
    # Return only non-sensitive account identifiers needed to prove routing.
    return {
        "mid": data.get("mid"),
        "name": data.get("name"),
        "level": data.get("level"),
        "vip_type": data.get("vip", {}).get("type") if isinstance(data.get("vip"), dict) else None,
    }


def probe_self_info(secret_root: Path, account_profile: str) -> dict[str, Any]:
    return asyncio.run(_probe_self_info_async(secret_root, account_profile))


def import_status() -> dict[str, Any]:
    try:
        import bilibili_api  # type: ignore  # noqa: F401
    except Exception as exc:  # pragma: no cover - exact env-dependent message
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"available": True, "module": "bilibili_api"}


def build_plan(secret_root: Path, account_profile: str) -> dict[str, Any]:
    credential_check = check_credentials(secret_root, account_profile)
    plan: dict[str, Any] = {
        "backend": "bilibili-api-python",
        "credential_check": credential_check.to_public_dict(),
        "import_status": import_status(),
        "note": "Live API calls are intentionally not made during plan/dry-run.",
    }

    if credential_check.usable and plan["import_status"]["available"]:
        try:
            credential = create_credential(secret_root, account_profile)
            plan["credential_object"] = {
                "constructable": True,
                "has_sessdata": bool(credential.has_sessdata()),
                "has_bili_jct": bool(credential.has_bili_jct()),
                "has_buvid3": bool(credential.has_buvid3()),
            }
        except Exception as exc:  # pragma: no cover - defensive, env-dependent
            plan["credential_object"] = {
                "constructable": False,
                "error": f"{type(exc).__name__}: {exc}",
            }

    return plan
