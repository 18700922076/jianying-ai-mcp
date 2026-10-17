from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path


TESTED_JIANYING_VERSION = "10.0.5.13816"
TESTED_DRAFT_LIBRARY_VERSION = "0.3.0"


@dataclass(frozen=True)
class Settings:
    draft_root: Path
    work_root: Path
    jianying_exe: Path | None
    ffmpeg_exe: Path | None = None
    ffprobe_exe: Path | None = None
    yt_dlp_exe: Path | None = None
    draft_crypto_exe: Path | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        draft_root = Path(
            os.environ.get(
                "JIANYING_DRAFT_ROOT",
                str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft"),
            )
        ).expanduser().resolve()
        work_root = Path(
            os.environ.get("JIANYING_MCP_WORK_ROOT", str(Path.cwd() / ".jianying-mcp"))
        ).expanduser().resolve()
        raw_exe = os.environ.get("JIANYING_EXE")
        exe = Path(raw_exe).expanduser().resolve() if raw_exe else _find_jianying_exe()
        return cls(
            draft_root=draft_root,
            work_root=work_root,
            jianying_exe=exe,
            ffmpeg_exe=_find_media_tool("ffmpeg", "JIANYING_FFMPEG"),
            ffprobe_exe=_find_media_tool("ffprobe", "JIANYING_FFPROBE"),
            yt_dlp_exe=_find_media_tool("yt-dlp", "JIANYING_YT_DLP"),
            draft_crypto_exe=_find_draft_crypto(),
        )


def _find_jianying_exe() -> Path | None:
    roots = [Path(os.environ.get("LOCALAPPDATA", "")) / "JianyingPro"]
    matches: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        matches.extend(root.glob("**/JianyingPro.exe"))
        direct = root / "JianyingPro.exe"
        if direct.is_file():
            matches.append(direct)
    if not matches:
        return None
    return sorted(matches, key=lambda path: _version_tuple(path.parent.name), reverse=True)[0].resolve()


def _find_media_tool(name: str, env_name: str) -> Path | None:
    configured = os.environ.get(env_name)
    if configured:
        path = Path(configured).expanduser().resolve()
        return path if path.is_file() else None

    found = shutil.which(name)
    if found:
        return Path(found).resolve()

    return None


def _find_draft_crypto() -> Path | None:
    configured = _find_media_tool("jy-draftc", "JIANYING_DRAFT_CRYPTO")
    if configured:
        return configured
    local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
    candidates = [
        Path.cwd() / ".jianying-mcp" / "tools" / "jy-draftc.exe",
        local_app_data / "jianying-ai-mcp" / "bin" / "jy-draftc.exe",
    ]
    return next((path.resolve() for path in candidates if path.is_file()), None)


def _version_tuple(value: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", value)
    return tuple(int(num) for num in nums) if nums else (0,)


def detected_jianying_version(settings: Settings) -> str | None:
    if settings.jianying_exe is None:
        return None
    folder_version = settings.jianying_exe.parent.name
    return folder_version if re.fullmatch(r"\d+(?:\.\d+)+", folder_version) else None


def compatibility_notice(settings: Settings) -> dict[str, object]:
    detected = detected_jianying_version(settings)
    matches = detected == TESTED_JIANYING_VERSION
    if detected is None:
        message = (
            f"未检测到剪映版本；能力基线仅在 {TESTED_JIANYING_VERSION} 验证，"
            "生成后必须在剪映中抽样确认。"
        )
    elif matches:
        message = f"当前剪映 {detected} 与已验证基线一致。"
    else:
        message = (
            f"当前剪映 {detected} 与已验证版本 {TESTED_JIANYING_VERSION} 不一致；"
            "动画、在线资源、滤镜和草稿格式可能失效或显示不同，生成后必须抽样确认。"
        )
    return {
        "detected": detected,
        "tested": TESTED_JIANYING_VERSION,
        "matches_tested_version": matches,
        "message": message,
    }
