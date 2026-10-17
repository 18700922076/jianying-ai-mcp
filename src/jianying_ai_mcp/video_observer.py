from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import Settings
from .video_whisper import transcribe_video


DETAIL_CAPS = {"transcript": 0, "efficient": 50, "balanced": 100, "dense": 200}
TIMESTAMP_RE = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")
SRT_TIME_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})"
)
VTT_TIME_RE = re.compile(
    r"(?:(\d{1,2}):)?(\d{2}):(\d{2})[.](\d{3})\s*-->\s*"
    r"(?:(\d{1,2}):)?(\d{2}):(\d{2})[.](\d{3})"
)


class VideoObserverError(ValueError):
    pass


def observer_status(settings: Settings) -> dict[str, Any]:
    return {
        "available": settings.ffmpeg_exe is not None and settings.ffprobe_exe is not None,
        "ffmpeg": str(settings.ffmpeg_exe) if settings.ffmpeg_exe else None,
        "ffprobe": str(settings.ffprobe_exe) if settings.ffprobe_exe else None,
        "yt_dlp": str(settings.yt_dlp_exe) if settings.yt_dlp_exe else None,
        "url_input": settings.yt_dlp_exe is not None,
        "transcription": {
            "sidecar_subtitles": True,
            "groq_whisper": bool(os.environ.get("GROQ_API_KEY")),
            "openai_whisper": bool(os.environ.get("OPENAI_API_KEY")),
        },
        "setup_hint": (
            "设置 JIANYING_FFMPEG/JIANYING_FFPROBE；URL 输入另设置 JIANYING_YT_DLP。"
            if settings.ffmpeg_exe is None or settings.ffprobe_exe is None else None
        ),
    }


def _require_tool(path: Path | None, name: str) -> Path:
    if path is None or not path.is_file():
        raise VideoObserverError(f"未找到 {name}；请在环境变量中配置对应可执行文件")
    return path


def _run(command: list[str], label: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-1200:]
        raise VideoObserverError(f"{label}失败: {detail}")
    return result


def _parse_time(value: str | float | int | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    parts = value.strip().split(":")
    try:
        if len(parts) == 1:
            return float(parts[0].removesuffix("s"))
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError as exc:
        raise VideoObserverError(f"无效时间: {value}") from exc
    raise VideoObserverError(f"无效时间: {value}")


def _analysis_id(source: str, options: dict[str, Any]) -> str:
    path = Path(source).expanduser()
    identity: dict[str, Any] = {"source": source, "options": options}
    if path.is_file():
        stat = path.stat()
        identity.update({"source": str(path.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    subtitle = options.get("subtitle_path")
    if subtitle:
        subtitle_file = Path(str(subtitle)).expanduser()
        if subtitle_file.is_file():
            stat = subtitle_file.stat()
            identity["subtitle"] = {
                "path": str(subtitle_file.resolve()),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
    payload = json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:20]


def _download_url(settings: Settings, source: str, work_dir: Path) -> Path:
    yt_dlp = _require_tool(settings.yt_dlp_exe, "yt-dlp")
    template = work_dir / "source.%(ext)s"
    result = _run([
        str(yt_dlp), "--no-playlist", "--write-subs", "--write-auto-subs",
        "--sub-langs", "zh.*,en.*", "--sub-format", "vtt", "--print", "after_move:filepath",
        "-o", str(template), source,
    ], "下载视频")
    candidates = [Path(line.strip()) for line in result.stdout.splitlines() if line.strip()]
    for candidate in reversed(candidates):
        if candidate.is_file() and candidate.suffix.lower() not in {".vtt", ".srt"}:
            return candidate.resolve()
    files = [p for p in work_dir.glob("source.*") if p.suffix.lower() not in {".vtt", ".srt", ".json"}]
    if not files:
        raise VideoObserverError("yt-dlp 未返回下载后的视频路径")
    return files[0].resolve()


def _resolve_source(settings: Settings, source: str, work_dir: Path) -> tuple[Path, bool]:
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        return _download_url(settings, source, work_dir), True
    path = Path(source).expanduser()
    if not path.is_absolute():
        raise VideoObserverError("本地视频必须使用绝对路径")
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    return path, False


def _probe(ffprobe: Path, video: Path) -> dict[str, Any]:
    result = _run([
        str(ffprobe), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video),
    ], "读取视频信息")
    data = json.loads(result.stdout)
    streams = data.get("streams") or []
    visual = next((s for s in streams if s.get("codec_type") == "video"), {})
    duration = float((data.get("format") or {}).get("duration") or visual.get("duration") or 0)
    return {
        "duration_ms": round(duration * 1000),
        "width": visual.get("width"),
        "height": visual.get("height"),
        "codec": visual.get("codec_name"),
        "fps": visual.get("avg_frame_rate"),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def _scale_filter(width: int) -> str:
    if width < 64 or width > 1920:
        raise VideoObserverError("resolution 必须在 64 到 1920 之间")
    return f"scale=min({width}\\,iw):-2"


def _extract_candidates(
    ffmpeg: Path,
    video: Path,
    frame_dir: Path,
    detail: str,
    resolution: int,
    start: float,
    end: float,
) -> list[dict[str, Any]]:
    frame_dir.mkdir(parents=True, exist_ok=True)
    output = frame_dir / "frame_%05d.jpg"
    command = [str(ffmpeg), "-hide_banner", "-loglevel", "info", "-y"]
    if start > 0:
        command += ["-ss", f"{start:.3f}"]
    command += ["-i", str(video)]
    duration = max(0.0, end - start)
    if duration > 0:
        command += ["-t", f"{duration:.3f}"]
    if detail == "efficient":
        command += ["-skip_frame", "nokey", "-vf", f"{_scale_filter(resolution)},showinfo"]
    elif detail in {"balanced", "dense"}:
        threshold = 0.24 if detail == "balanced" else 0.15
        command += ["-vf", f"select='gt(scene,{threshold})',{_scale_filter(resolution)},showinfo"]
    else:
        return []
    command += ["-fps_mode", "vfr", "-q:v", "4", "-frames:v", "1000", str(output)]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    produced = sorted(frame_dir.glob("frame_*.jpg"))
    if result.returncode != 0 and not produced and detail in {"balanced", "dense"}:
        # ffmpeg can return an encoder error when the scene selector emits no
        # frames. This is a normal static-video case; the caller will use the
        # uniform fallback.
        return []
    if result.returncode != 0:
        detail_text = (result.stderr or result.stdout).strip()[-1200:]
        raise VideoObserverError(f"提取视频代表帧失败: {detail_text}")
    times = [start + float(match.group(1)) for match in TIMESTAMP_RE.finditer(result.stderr)]
    files = produced
    return [
        {
            "time_ms": round((times[index] if index < len(times) else start) * 1000),
            "path": str(path.resolve()),
            "reason": "keyframe" if detail == "efficient" else "scene",
        }
        for index, path in enumerate(files)
    ]


def _uniform_frames(
    ffmpeg: Path,
    video: Path,
    frame_dir: Path,
    resolution: int,
    start: float,
    end: float,
    target: int,
) -> list[dict[str, Any]]:
    for path in frame_dir.glob("frame_*.jpg"):
        path.unlink()
    duration = max(0.001, end - start)
    fps = min(2.0, max(0.01, target / duration))
    output = frame_dir / "frame_%05d.jpg"
    command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y"]
    if start > 0:
        command += ["-ss", f"{start:.3f}"]
    command += ["-i", str(video), "-t", f"{duration:.3f}", "-vf", f"fps={fps:.6f},{_scale_filter(resolution)}", "-q:v", "4", str(output)]
    _run(command, "均匀提取视频帧")
    files = sorted(frame_dir.glob("frame_*.jpg"))[:target]
    return [
        {"time_ms": round((start + index / fps) * 1000), "path": str(path.resolve()), "reason": "uniform"}
        for index, path in enumerate(files)
    ]


def _dedupe_exact(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    kept = []
    for frame in frames:
        digest = hashlib.sha1(Path(frame["path"]).read_bytes()).hexdigest()
        if digest in seen:
            Path(frame["path"]).unlink(missing_ok=True)
            continue
        seen.add(digest)
        kept.append(frame)
    return kept


def _even_sample(frames: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    if len(frames) <= cap:
        return frames
    indices = {round(i * (len(frames) - 1) / (cap - 1)) for i in range(cap)} if cap > 1 else {0}
    kept = [frame for index, frame in enumerate(frames) if index in indices]
    keep_paths = {frame["path"] for frame in kept}
    for frame in frames:
        if frame["path"] not in keep_paths:
            Path(frame["path"]).unlink(missing_ok=True)
    return kept


def _timestamp(groups: tuple[str | None, ...]) -> int:
    h, m, s, ms = groups
    return (int(h or 0) * 3600 + int(m) * 60 + int(s)) * 1000 + int(ms)


def parse_subtitle_file(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = text.splitlines()
    segments: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        match = SRT_TIME_RE.match(line) or VTT_TIME_RE.match(line)
        if not match:
            index += 1
            continue
        values = match.groups()
        start_groups = values[:4]
        end_groups = values[4:]
        index += 1
        body = []
        while index < len(lines) and lines[index].strip():
            cleaned = re.sub(r"<[^>]+>", "", lines[index]).strip()
            if cleaned:
                body.append(cleaned)
            index += 1
        if body:
            segments.append({
                "start_ms": _timestamp(start_groups),
                "end_ms": _timestamp(end_groups),
                "text": " ".join(body),
            })
        index += 1
    return segments


def _find_subtitle(video: Path, explicit: str | None, work_dir: Path) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    candidates = []
    for parent in {video.parent, work_dir}:
        candidates.extend(parent.glob(f"{video.stem}*.srt"))
        candidates.extend(parent.glob(f"{video.stem}*.vtt"))
        candidates.extend(parent.glob("source*.srt"))
        candidates.extend(parent.glob("source*.vtt"))
    return sorted(set(candidates))[0] if candidates else None


def _write_srt(segments: list[dict[str, Any]], path: Path) -> None:
    def stamp(ms: int) -> str:
        hours, remain = divmod(ms, 3_600_000)
        minutes, remain = divmod(remain, 60_000)
        seconds, millis = divmod(remain, 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"
    blocks = [f"{i}\n{stamp(s['start_ms'])} --> {stamp(s['end_ms'])}\n{s['text']}" for i, s in enumerate(segments, 1)]
    path.write_text("\n\n".join(blocks) + ("\n" if blocks else ""), encoding="utf-8")


def analyze_video(
    settings: Settings,
    source: str,
    *,
    detail: str = "efficient",
    start: str | float | int | None = None,
    end: str | float | int | None = None,
    max_frames: int | None = None,
    resolution: int = 512,
    subtitle_path: str | None = None,
    transcription: str = "auto",
) -> dict[str, Any]:
    if detail not in DETAIL_CAPS:
        raise VideoObserverError(f"detail 必须是 {sorted(DETAIL_CAPS)}")
    if transcription not in {"auto", "none", "groq", "openai"}:
        raise VideoObserverError("transcription 必须是 auto/none/groq/openai")
    cap = DETAIL_CAPS[detail] if max_frames is None else max_frames
    if cap < 0 or cap > 500:
        raise VideoObserverError("max_frames 必须在 0 到 500 之间")
    options = {
        "detail": detail, "start": start, "end": end, "max_frames": cap,
        "resolution": resolution, "subtitle_path": subtitle_path, "transcription": transcription,
    }
    analysis_id = _analysis_id(source, options)
    work_dir = settings.work_root / "video_analyses" / analysis_id
    report_path = work_dir / "analysis.json"
    if report_path.is_file():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["cache_hit"] = True
        return _compact_report(report)

    ffmpeg = _require_tool(settings.ffmpeg_exe, "ffmpeg")
    ffprobe = _require_tool(settings.ffprobe_exe, "ffprobe")
    work_dir.mkdir(parents=True, exist_ok=True)
    video, downloaded = _resolve_source(settings, source, work_dir)
    metadata = _probe(ffprobe, video)
    full_end = metadata["duration_ms"] / 1000
    start_s = _parse_time(start) or 0.0
    end_s = _parse_time(end) if end is not None else full_end
    if start_s < 0 or end_s <= start_s or (full_end and start_s >= full_end):
        raise VideoObserverError("分析时间范围无效")
    end_s = min(end_s, full_end) if full_end else end_s

    subtitle = _find_subtitle(video, subtitle_path, work_dir)
    transcript: list[dict[str, Any]] = parse_subtitle_file(subtitle) if subtitle else []
    transcript_source = "sidecar" if transcript else None
    if not transcript and transcription != "none" and metadata["has_audio"]:
        backend = transcription
        if backend == "auto":
            backend = "groq" if os.environ.get("GROQ_API_KEY") else "openai" if os.environ.get("OPENAI_API_KEY") else "none"
        if backend != "none":
            api_key = os.environ.get("GROQ_API_KEY" if backend == "groq" else "OPENAI_API_KEY")
            if not api_key:
                raise VideoObserverError(f"transcription={backend} 但未设置对应 API Key")
            transcript = transcribe_video(video, work_dir, ffmpeg, ffprobe, backend, api_key)
            transcript_source = f"whisper:{backend}"
    transcript = [s for s in transcript if s["end_ms"] >= round(start_s * 1000) and s["start_ms"] <= round(end_s * 1000)]
    srt_path = work_dir / "transcript.srt"
    if transcript:
        _write_srt(transcript, srt_path)

    frames: list[dict[str, Any]] = []
    if cap:
        frame_dir = work_dir / "frames"
        frames = _extract_candidates(ffmpeg, video, frame_dir, detail, resolution, start_s, end_s)
        if len(frames) < min(4, cap):
            frames = _uniform_frames(ffmpeg, video, frame_dir, resolution, start_s, end_s, min(cap, max(4, round(end_s - start_s))))
        frames = _even_sample(_dedupe_exact(frames), cap)

    report = {
        "success": True,
        "analysis_id": analysis_id,
        "cache_hit": False,
        "source": source,
        "local_video_path": str(video),
        "downloaded": downloaded,
        "metadata": metadata,
        "range": {"start_ms": round(start_s * 1000), "end_ms": round(end_s * 1000)},
        "detail": detail,
        "frames": frames,
        "frame_count": len(frames),
        "transcript": transcript,
        "transcript_source": transcript_source,
        "transcript_srt_path": str(srt_path) if transcript else None,
        "report_path": str(report_path),
        "privacy": "视频和画面留在本机；仅在启用 Whisper API 且无字幕时上传压缩音频。",
        "limitations": ["代表帧不是完整视频流", "字幕为语句级而非逐字对齐", "精确卡点和口型同步需要额外分析"],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return _compact_report(report)


def _compact_report(report: dict[str, Any], transcript_limit: int = 120) -> dict[str, Any]:
    result = dict(report)
    transcript = list(report.get("transcript") or [])
    result["transcript_total"] = len(transcript)
    result["transcript_truncated"] = len(transcript) > transcript_limit
    result["transcript"] = transcript[:transcript_limit]
    return result


def get_analysis_context(
    settings: Settings,
    analysis_id: str,
    *,
    start_ms: int | None = None,
    end_ms: int | None = None,
    offset: int = 0,
    limit: int = 120,
) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{20}", analysis_id):
        raise VideoObserverError("analysis_id 格式无效")
    if offset < 0 or limit < 1 or limit > 500:
        raise VideoObserverError("offset 必须非负，limit 必须在 1 到 500 之间")
    report_path = (settings.work_root / "video_analyses" / analysis_id / "analysis.json").resolve()
    if not report_path.is_file():
        raise FileNotFoundError(report_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    range_start = int(report["range"]["start_ms"] if start_ms is None else start_ms)
    range_end = int(report["range"]["end_ms"] if end_ms is None else end_ms)
    if range_start < 0 or range_end <= range_start:
        raise VideoObserverError("上下文时间范围无效")
    transcript = [
        item for item in report.get("transcript", [])
        if int(item["end_ms"]) >= range_start and int(item["start_ms"]) <= range_end
    ]
    frames = [
        item for item in report.get("frames", [])
        if range_start <= int(item["time_ms"]) <= range_end
    ]
    page = transcript[offset:offset + limit]
    observations_path = report_path.parent / "observations.json"
    return {
        "success": True,
        "analysis_id": analysis_id,
        "range": {"start_ms": range_start, "end_ms": range_end},
        "metadata": report.get("metadata"),
        "frames": frames,
        "transcript": page,
        "transcript_page": {
            "offset": offset,
            "returned": len(page),
            "total_in_range": len(transcript),
            "has_more": offset + len(page) < len(transcript),
            "next_offset": offset + len(page) if offset + len(page) < len(transcript) else None,
        },
        "transcript_srt_path": report.get("transcript_srt_path"),
        "observations_path": str(observations_path) if observations_path.is_file() else None,
        "report_path": str(report_path),
    }


def save_observations(
    settings: Settings,
    analysis_id: str,
    observations: list[dict[str, Any]],
    edit_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{20}", analysis_id):
        raise VideoObserverError("analysis_id 格式无效")
    root = (settings.work_root / "video_analyses" / analysis_id).resolve()
    report_path = root / "analysis.json"
    if not report_path.is_file():
        raise FileNotFoundError(report_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    analysis_start_ms = int(report["range"]["start_ms"])
    end_ms = int(report["range"]["end_ms"])
    for item in observations:
        time_ms = int(item.get("time_ms", -1))
        if time_ms < analysis_start_ms or time_ms > end_ms or not str(item.get("description", "")).strip():
            raise VideoObserverError("每条 observation 需要范围内的 time_ms 和 description")
    for item in edit_candidates or []:
        start_ms = int(item.get("start_ms", -1)); candidate_end = int(item.get("end_ms", -1))
        if start_ms < analysis_start_ms or candidate_end <= start_ms or candidate_end > end_ms:
            raise VideoObserverError("edit_candidate 时间范围无效")
    payload = {
        "analysis_id": analysis_id,
        "observations": observations,
        "edit_candidates": edit_candidates or [],
    }
    output = root / "observations.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"success": True, "analysis_id": analysis_id, "observations_path": str(output), "counts": {"observations": len(observations), "edit_candidates": len(edit_candidates or [])}}
