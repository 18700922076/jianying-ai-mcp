from __future__ import annotations

import base64
import binascii
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings


class DraftCryptoError(RuntimeError):
    pass


@dataclass(frozen=True)
class LoadedDraftJson:
    content: dict[str, Any]
    source_format: str


def classify_draft_bytes(raw: bytes) -> str:
    try:
        parsed = json.loads(raw.decode("utf-8-sig"))
        if isinstance(parsed, dict):
            return "plaintext_json"
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass

    compact = b"".join(raw.split())
    if len(compact) >= 32 and len(compact) % 4 == 0:
        try:
            decoded = base64.b64decode(compact, validate=True)
        except (binascii.Error, ValueError):
            decoded = b""
        if len(decoded) >= 16:
            return "jianying_encrypted"
    return "unknown"


def crypto_status(settings: Settings) -> dict[str, Any]:
    helper = settings.draft_crypto_exe
    install_dir = settings.jianying_exe.parent if settings.jianying_exe else None
    dll = install_dir / "videoeditor.dll" if install_dir else None
    reasons: list[str] = []
    if os.name != "nt":
        reasons.append("加密草稿读取器当前只支持 Windows")
    if helper is None or not helper.is_file():
        reasons.append("未找到 jy-draftc 兼容读取器；请配置 JIANYING_DRAFT_CRYPTO")
    if dll is None or not dll.is_file():
        reasons.append("剪映安装目录中未找到 videoeditor.dll")
    return {
        "available": not reasons,
        "mode": "read_copy_only",
        "helper": str(helper) if helper else None,
        "jianying_dll": str(dll) if dll and dll.is_file() else None,
        "source_is_never_modified": True,
        "plaintext_is_temporary": True,
        "reasons": reasons,
    }


def _validate_json(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DraftCryptoError(f"{label} 解密结果不是 JSON 对象")
    return value


def _decrypt_with_helper(settings: Settings, source: Path, *, label: str) -> dict[str, Any]:
    status = crypto_status(settings)
    if not status["available"]:
        raise DraftCryptoError("；".join(status["reasons"]))
    assert settings.draft_crypto_exe is not None
    assert settings.jianying_exe is not None

    settings.work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="draft-decrypt-", dir=settings.work_root) as raw_temp:
        temp = Path(raw_temp)
        encrypted_copy = temp / "input.enc"
        output = temp / "output.json"
        shutil.copy2(source, encrypted_copy)
        command = [
            str(settings.draft_crypto_exe),
            "--decrypt",
            str(encrypted_copy),
            str(output),
            "--install-dir",
            str(settings.jianying_exe.parent),
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = subprocess.run(
                command,
                cwd=temp,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=45,
                check=False,
                creationflags=creationflags,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DraftCryptoError(f"{label} 调用本地读取器失败: {exc}") from exc
        if completed.returncode != 0 or not output.is_file():
            detail = (completed.stderr or completed.stdout).strip()[-1200:]
            raise DraftCryptoError(f"{label} 解密失败（code={completed.returncode}）: {detail}")
        try:
            return _validate_json(json.loads(output.read_text(encoding="utf-8-sig")), label)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DraftCryptoError(f"{label} 解密结果不是有效 UTF-8 JSON") from exc


def load_draft_json(settings: Settings, source: Path, *, label: str = "草稿") -> LoadedDraftJson:
    raw = source.read_bytes()
    source_format = classify_draft_bytes(raw)
    if source_format == "plaintext_json":
        return LoadedDraftJson(_validate_json(json.loads(raw.decode("utf-8-sig")), label), source_format)
    if source_format == "jianying_encrypted":
        return LoadedDraftJson(_decrypt_with_helper(settings, source, label=label), source_format)
    raise DraftCryptoError(f"{label}既不是有效明文 JSON，也不像受支持的剪映加密草稿")
