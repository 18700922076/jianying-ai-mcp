from __future__ import annotations

import io
import json
import math
import mimetypes
import subprocess
import urllib.error
import uuid
from pathlib import Path
from urllib.request import Request, urlopen


MAX_UPLOAD_BYTES = 24 * 1024 * 1024
ENDPOINTS = {
    "groq": ("https://api.groq.com/openai/v1/audio/transcriptions", "whisper-large-v3"),
    "openai": ("https://api.openai.com/v1/audio/transcriptions", "whisper-1"),
}


def _run(command: list[str], label: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"{label}失败: {result.stderr.strip()}")
    return result


def _multipart(fields: dict[str, str], file_path: Path) -> tuple[bytes, str]:
    boundary = f"----JianyingAI{uuid.uuid4().hex}"
    eol = b"\r\n"
    buffer = io.BytesIO()
    for name, value in fields.items():
        buffer.write(f"--{boundary}".encode()); buffer.write(eol)
        buffer.write(f'Content-Disposition: form-data; name="{name}"'.encode()); buffer.write(eol * 2)
        buffer.write(value.encode()); buffer.write(eol)
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    buffer.write(f"--{boundary}".encode()); buffer.write(eol)
    buffer.write(f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"'.encode()); buffer.write(eol)
    buffer.write(f"Content-Type: {mime}".encode()); buffer.write(eol * 2)
    buffer.write(file_path.read_bytes()); buffer.write(eol)
    buffer.write(f"--{boundary}--".encode()); buffer.write(eol)
    return buffer.getvalue(), boundary


def _post(audio_path: Path, backend: str, api_key: str) -> list[dict]:
    endpoint, model = ENDPOINTS[backend]
    body, boundary = _multipart(
        {"model": model, "response_format": "verbose_json", "temperature": "0"},
        audio_path,
    )
    request = Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "jianying-ai-mcp/0.2",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=300) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"{backend} Whisper 请求失败: HTTP {exc.code} {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{backend} Whisper 请求失败: {exc}") from exc
    segments = []
    for item in data.get("segments") or []:
        text = str(item.get("text") or "").strip()
        if text:
            segments.append({
                "start_ms": round(float(item.get("start") or 0) * 1000),
                "end_ms": round(float(item.get("end") or 0) * 1000),
                "text": text,
            })
    if not segments and str(data.get("text") or "").strip():
        segments.append({"start_ms": 0, "end_ms": 0, "text": str(data["text"]).strip()})
    return segments


def _duration(ffprobe: Path, audio: Path) -> float:
    result = _run([
        str(ffprobe), "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(audio),
    ], "读取音频时长")
    return float(result.stdout.strip() or 0)


def transcribe_video(
    video_path: Path,
    work_dir: Path,
    ffmpeg: Path,
    ffprobe: Path,
    backend: str,
    api_key: str,
) -> list[dict]:
    """Extract compact audio and return segment timestamps from Whisper-compatible APIs.

    Only audio is uploaded; the source video and extracted frames remain local.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    audio = work_dir / "audio.mp3"
    _run([
        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-i", str(video_path),
        "-vn", "-acodec", "libmp3lame", "-ar", "16000", "-ac", "1", "-b:a", "64k", str(audio),
    ], "提取转写音频")
    if not audio.is_file() or audio.stat().st_size == 0:
        raise RuntimeError("没有生成可转写音频")

    if audio.stat().st_size <= MAX_UPLOAD_BYTES:
        return _post(audio, backend, api_key)

    duration = _duration(ffprobe, audio)
    count = math.ceil(audio.stat().st_size / MAX_UPLOAD_BYTES)
    chunk_duration = duration / count
    merged: list[dict] = []
    for index in range(count):
        offset = index * chunk_duration
        length = duration - offset if index == count - 1 else chunk_duration
        chunk = work_dir / f"audio_{index:03d}.mp3"
        _run([
            str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{offset:.3f}",
            "-i", str(audio), "-t", f"{length:.3f}", "-c", "copy", str(chunk),
        ], "切分转写音频")
        for segment in _post(chunk, backend, api_key):
            segment["start_ms"] += round(offset * 1000)
            segment["end_ms"] += round(offset * 1000)
            merged.append(segment)
    return merged
