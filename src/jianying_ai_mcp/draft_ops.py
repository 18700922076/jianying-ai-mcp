from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

import pyJianYingDraft as draft
from pyJianYingDraft.metadata import AudioSceneEffectType, SpeechToSongType, ToneEffectType

from .config import Settings, compatibility_notice
from .draft_crypto import DraftCryptoError, load_draft_json


INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
SUPPORTED_TRACKS = {"video", "audio", "text", "sticker", "effect", "filter"}


class ManifestError(ValueError):
    pass


def _safe_name(value: str, label: str = "名称") -> str:
    value = value.strip()
    if not value or value in {".", ".."} or INVALID_NAME.search(value):
        raise ManifestError(f"{label}包含空值或 Windows 路径非法字符: {value!r}")
    return value


def _child(root: Path, name: str) -> Path:
    path = (root / _safe_name(name)).resolve()
    if path.parent != root.resolve():
        raise ManifestError(f"路径越界: {path}")
    return path


def _enum(enum_type: Any, name: str, label: str) -> Any:
    members = getattr(enum_type, "__members__", {})
    if name in members:
        return members[name]
    for member in members.values():
        meta = getattr(member, "value", None)
        if getattr(meta, "name", None) == name:
            return member
    sample = list(members)[:12]
    raise ManifestError(f"未知{label}: {name!r}；示例值: {sample}")


def _timerange(item: dict[str, Any], prefix: str = "") -> Any:
    start = item.get(f"{prefix}start", "0s")
    duration = item.get(f"{prefix}duration")
    if duration is None:
        raise ManifestError(f"片段缺少 {prefix}duration")
    return draft.trange(start, duration)


def _source_timerange(item: dict[str, Any]) -> Any | None:
    if "source_duration" not in item:
        return None
    return draft.trange(item.get("source_start", "0s"), item["source_duration"])


def _clip(data: dict[str, Any] | None) -> Any | None:
    if not data:
        return None
    allowed = {
        "alpha",
        "flip_horizontal",
        "flip_vertical",
        "rotation",
        "scale_x",
        "scale_y",
        "transform_x",
        "transform_y",
    }
    unknown = set(data) - allowed
    if unknown:
        raise ManifestError(f"未知 clip 参数: {sorted(unknown)}")
    return draft.ClipSettings(**data)


def _rgb(value: Any, label: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ManifestError(f"{label}必须是三个 0~1 数值")
    values = tuple(float(v) for v in value)
    if any(v < 0 or v > 1 for v in values):
        raise ManifestError(f"{label}必须是三个 0~1 数值")
    return values


def _text_style(data: dict[str, Any] | None) -> Any:
    data = dict(data or {})
    if "color" in data:
        data["color"] = _rgb(data["color"], "文本颜色")
    return draft.TextStyle(**data)


def _border(data: dict[str, Any] | None) -> Any | None:
    if not data:
        return None
    data = dict(data)
    if "color" in data:
        data["color"] = _rgb(data["color"], "描边颜色")
    return draft.TextBorder(**data)


def _shadow(data: dict[str, Any] | None) -> Any | None:
    if not data:
        return None
    data = dict(data)
    if "color" in data:
        data["color"] = _rgb(data["color"], "阴影颜色")
    return draft.TextShadow(**data)


def _add_visual_features(segment: Any, item: dict[str, Any], *, text: bool = False) -> None:
    if item.get("mask") or item.get("background_filling"):
        raise ManifestError("mask 和 background_filling 在当前验证版本不可见，首版拒绝写入")

    transition = item.get("transition")
    if transition:
        segment.add_transition(
            _enum(draft.TransitionType, transition["name"], "转场"),
            duration=transition.get("duration"),
        )

    filter_spec = item.get("filter")
    if filter_spec:
        segment.add_filter(
            _enum(draft.FilterType, filter_spec["name"], "滤镜"),
            intensity=float(filter_spec.get("intensity", 100)),
        )

    for effect in item.get("effects", []):
        segment.add_effect(
            _enum(draft.VideoSceneEffectType, effect["name"], "画面特效"),
            params=effect.get("params"),
        )

    if item.get("mix_mode"):
        segment.set_mix_mode(_enum(draft.MixModeType, item["mix_mode"], "混合模式"))

    if item.get("chroma"):
        chroma = item["chroma"]
        color = chroma["color"]
        if not re.fullmatch(r"#[0-9A-Fa-f]{8}", color):
            raise ManifestError("色度抠图颜色必须是 #RRGGBBAA")
        segment.add_chroma(
            color,
            intensity=float(chroma.get("intensity", 100)),
            edge_smooth=float(chroma.get("edge_smooth", 0)),
            spill=float(chroma.get("spill", 0)),
        )

    for animation in item.get("animations", []):
        category = animation["category"]
        enum_type = {
            "text_intro": draft.TextIntro,
            "text_outro": draft.TextOutro,
            "text_loop": draft.TextLoopAnim,
            "video_intro": draft.IntroType,
            "video_outro": draft.OutroType,
            "video_group": draft.GroupAnimationType,
        }.get(category)
        if enum_type is None or (text and not category.startswith("text_")) or (not text and category.startswith("text_")):
            raise ManifestError(f"动画分类与片段类型不匹配: {category}")
        segment.add_animation(
            _enum(enum_type, animation["name"], "动画"),
            duration=animation.get("duration"),
        )

    for keyframe in item.get("keyframes", []):
        prop = _enum(draft.KeyframeProperty, keyframe["property"], "关键帧属性")
        if prop == draft.KeyframeProperty.volume:
            raise ManifestError("音量关键帧未完成实际验证，首版拒绝写入")
        segment.add_keyframe(prop, keyframe["time"], float(keyframe["value"]))


def _video_segment(item: dict[str, Any]) -> Any:
    source = Path(item["source"]).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    segment = draft.VideoSegment(
        str(source),
        _timerange(item),
        source_timerange=_source_timerange(item),
        speed=item.get("speed"),
        volume=float(item.get("volume", 1.0)),
        change_pitch=bool(item.get("change_pitch", False)),
        clip_settings=_clip(item.get("clip")),
    )
    _add_visual_features(segment, item)
    return segment


def _audio_segment(item: dict[str, Any]) -> Any:
    source = Path(item["source"]).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if item.get("keyframes"):
        raise ManifestError("音量关键帧未完成实际验证，首版拒绝写入")
    segment = draft.AudioSegment(
        str(source),
        _timerange(item),
        source_timerange=_source_timerange(item),
        speed=item.get("speed"),
        volume=float(item.get("volume", 1.0)),
        change_pitch=bool(item.get("change_pitch", False)),
    )
    fade = item.get("fade")
    if fade:
        segment.add_fade(fade.get("in", "0s"), fade.get("out", "0s"))
    enum_types = {
        "scene": AudioSceneEffectType,
        "tone": ToneEffectType,
        "speech_to_song": SpeechToSongType,
    }
    used: set[str] = set()
    for effect in item.get("effects", []):
        category = effect["category"]
        if category in used:
            raise ManifestError(f"每个音频片段只能使用一个 {category} 效果")
        enum_type = enum_types.get(category)
        if enum_type is None:
            raise ManifestError(f"未知音频效果分类: {category}")
        segment.add_effect(_enum(enum_type, effect["name"], "音频效果"), effect.get("params"))
        used.add(category)
    return segment


def _text_segment(item: dict[str, Any]) -> Any:
    font = _enum(draft.FontType, item["font"], "字体") if item.get("font") else None
    background = draft.TextBackground(**item["background"]) if item.get("background") else None
    segment = draft.TextSegment(
        str(item["text"]),
        _timerange(item),
        font=font,
        style=_text_style(item.get("style")),
        clip_settings=_clip(item.get("clip")),
        border=_border(item.get("border")),
        background=background,
        shadow=_shadow(item.get("shadow")),
    )
    _add_visual_features(segment, item, text=True)
    if item.get("bubble"):
        segment.add_bubble(item["bubble"]["effect_id"], item["bubble"]["resource_id"])
    if item.get("flower_text_resource_id"):
        segment.add_effect(item["flower_text_resource_id"])
    return segment


def _sticker_segment(item: dict[str, Any]) -> Any:
    segment = draft.StickerSegment(
        str(item["resource_id"]),
        _timerange(item),
        clip_settings=_clip(item.get("clip")),
    )
    for keyframe in item.get("keyframes", []):
        prop = _enum(draft.KeyframeProperty, keyframe["property"], "贴纸关键帧属性")
        segment.add_keyframe(prop, keyframe["time"], float(keyframe["value"]))
    return segment


def _validate_content(path: Path) -> dict[str, Any]:
    content = json.loads((path / "draft_content.json").read_text(encoding="utf-8"))
    tracks = content.get("tracks")
    if not isinstance(tracks, list):
        raise ManifestError("生成的 draft_content.json 缺少 tracks")
    return {
        "duration_us": content.get("duration", 0),
        "tracks": len(tracks),
        "segments": sum(len(track.get("segments", [])) for track in tracks),
        "track_types": [track.get("type") for track in tracks],
    }


def _publish(staged: Path, target: Path) -> None:
    if target.exists():
        if (target / ".locked").exists():
            raise ManifestError(f"目标草稿正被剪映占用，拒绝覆盖: {target}")
        raise FileExistsError(f"目标草稿已存在，首版不自动覆盖: {target}")
    try:
        shutil.copytree(staged, target)
    except Exception:
        if target.exists() and not (target / ".locked").exists():
            shutil.rmtree(target)
        raise


def _update_meta_name(draft_path: Path, name: str) -> None:
    meta_path = draft_path / "draft_meta_info.json"
    if not meta_path.is_file():
        return
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return
    meta["draft_name"] = name
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def build_manifest(settings: Settings, manifest: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    settings.draft_root.mkdir(parents=True, exist_ok=True)
    settings.work_root.mkdir(parents=True, exist_ok=True)
    draft_spec = manifest.get("draft") or {}
    name = _safe_name(str(draft_spec.get("name", "")), "草稿名称")
    width = int(draft_spec.get("width", 1920))
    height = int(draft_spec.get("height", 1080))
    fps = int(draft_spec.get("fps", 30))
    if width <= 0 or height <= 0 or not 1 <= fps <= 120:
        raise ManifestError("分辨率或帧率无效")
    tracks = manifest.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        raise ManifestError("manifest.tracks 必须是非空数组")

    stage_root = _child(settings.work_root, f"stage-{uuid.uuid4().hex}")
    stage_root.mkdir(parents=True)
    staged_draft = stage_root / name
    try:
        folder = draft.DraftFolder(str(stage_root))
        script = folder.create_draft(name, width, height, fps=fps)
        seen: set[tuple[str, str]] = set()
        specs = []
        for track in tracks:
            track_type = str(track.get("type", ""))
            track_name = _safe_name(str(track.get("name", "")), "轨道名称")
            if track_type not in SUPPORTED_TRACKS:
                raise ManifestError(f"不支持的轨道类型: {track_type}")
            key = (track_type, track_name)
            if key in seen:
                raise ManifestError(f"轨道类型和名称重复: {key}")
            seen.add(key)
            specs.append(draft.TrackSpec(_enum(draft.TrackType, track_type, "轨道类型"), track_name))
        script.append_tracks(specs)

        for track in tracks:
            track_type = track["type"]
            track_name = track["name"]
            for item in track.get("segments", []):
                if track_type == "video":
                    segment = _video_segment(item)
                    script.add_segment(segment, track=track_name)
                elif track_type == "audio":
                    segment = _audio_segment(item)
                    script.add_segment(segment, track=track_name)
                elif track_type == "text":
                    segment = _text_segment(item)
                    script.add_segment(segment, track=track_name)
                elif track_type == "sticker":
                    segment = _sticker_segment(item)
                    script.add_segment(segment, track=track_name)
                elif track_type == "effect":
                    script.add_effect(
                        _enum(draft.VideoSceneEffectType, item["name"], "独立画面特效"),
                        _timerange(item),
                        track_name,
                        params=item.get("params"),
                    )
                elif track_type == "filter":
                    script.add_filter(
                        _enum(draft.FilterType, item["name"], "独立滤镜"),
                        _timerange(item),
                        track_name,
                        intensity=float(item.get("intensity", 100)),
                    )

        for srt in manifest.get("srt_imports", []):
            source = Path(srt["source"]).expanduser().resolve()
            if not source.is_file():
                raise FileNotFoundError(source)
            script.import_srt(
                str(source),
                track_name=_safe_name(srt.get("track_name", "字幕"), "字幕轨名称"),
                time_offset=srt.get("time_offset", 0.0),
                text_style=_text_style(srt.get("style")),
                clip_settings=_clip(srt.get("clip")),
            )
        script.save()
        summary = _validate_content(staged_draft)
        target = _child(settings.draft_root, name)
        if not dry_run:
            _publish(staged_draft, target)
        return {
            "success": True,
            "dry_run": dry_run,
            "draft_name": name,
            "draft_path": None if dry_run else str(target),
            "summary": summary,
            "compatibility": compatibility_notice(settings),
            "stages": ["校验编辑清单", "构建暂存草稿", "校验草稿结构", "发布到剪映草稿目录" if not dry_run else "完成试运行"],
        }
    finally:
        if stage_root.exists():
            shutil.rmtree(stage_root)


def _draft_content(settings: Settings, draft_name: str) -> tuple[Path, dict[str, Any], str]:
    path = _child(settings.draft_root, draft_name)
    content_path = path / "draft_content.json"
    if not content_path.is_file():
        raise FileNotFoundError(content_path)
    try:
        loaded = load_draft_json(settings, content_path, label=f"参考草稿 {draft_name}")
    except DraftCryptoError as exc:
        raise ManifestError(str(exc)) from exc
    return path, loaded.content, loaded.source_format


def analyze_reference_draft(settings: Settings, draft_name: str, *, include_text: bool = False) -> dict[str, Any]:
    draft_name = _safe_name(draft_name, "参考草稿名称")
    path, content, source_format = _draft_content(settings, draft_name)
    materials = content.get("materials", {})
    text_by_id: dict[str, str] = {}
    for material in materials.get("texts", []):
        try:
            body = json.loads(material.get("content", "{}"))
            text_by_id[material.get("id", "")] = body.get("text", "")
        except (TypeError, json.JSONDecodeError):
            continue
    tracks = []
    for index, track in enumerate(content.get("tracks", [])):
        segments = []
        for seg_index, segment in enumerate(track.get("segments", [])):
            t = segment.get("target_timerange", {})
            item: dict[str, Any] = {
                "index": seg_index,
                "start_us": t.get("start", 0),
                "duration_us": t.get("duration", 0),
                "extra_ref_count": len(segment.get("extra_material_refs", [])),
            }
            if include_text and segment.get("material_id") in text_by_id:
                item["text"] = text_by_id[segment["material_id"]]
            segments.append(item)
        tracks.append(
            {
                "index": index,
                "type": track.get("type"),
                "name": track.get("name", ""),
                "segments": segments,
            }
        )
    profile = {
        "reference_draft": draft_name,
        "source_format": source_format,
        "duration_us": content.get("duration", 0),
        "canvas": {
            "width": content.get("canvas_config", {}).get("width"),
            "height": content.get("canvas_config", {}).get("height"),
            "fps": content.get("fps"),
        },
        "tracks": tracks,
        "material_counts": {key: len(value) for key, value in materials.items() if isinstance(value, list) and value},
        "learnable_dimensions": ["轨道布局", "片段时间与节奏", "字幕样式", "动画/滤镜/特效引用", "混合与关键帧结构"],
        "limitations": ["加密草稿只读取临时副本，不修改原文件", "在线资源仍受剪映版本、账号和缓存影响"],
    }
    profile_root = settings.work_root / "reference_profiles"
    profile_root.mkdir(parents=True, exist_ok=True)
    profile_path = _child(profile_root, f"{draft_name}.json")
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "success": True,
        "profile_path": str(profile_path),
        "profile": profile,
        "compatibility": compatibility_notice(settings),
    }


def build_from_reference(
    settings: Settings,
    reference_draft: str,
    new_draft_name: str,
    replacements: list[dict[str, Any]] | None = None,
    text_replacements: list[dict[str, Any]] | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    reference_draft = _safe_name(reference_draft, "参考草稿名称")
    new_draft_name = _safe_name(new_draft_name, "新草稿名称")
    source, content, source_format = _draft_content(settings, reference_draft)
    if (source / ".locked").exists():
        raise ManifestError("参考草稿正被剪映占用；请返回首页后再复制")
    settings.work_root.mkdir(parents=True, exist_ok=True)
    stage_root = _child(settings.work_root, f"reference-{uuid.uuid4().hex}")
    stage_root.mkdir(parents=True)
    staged = stage_root / new_draft_name
    try:
        shutil.copytree(source, staged)
        (staged / "draft_content.json").write_text(
            json.dumps(content, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        meta_path = source / "draft_meta_info.json"
        if meta_path.is_file():
            try:
                loaded_meta = load_draft_json(settings, meta_path, label=f"参考草稿 {reference_draft} 元数据")
                (staged / "draft_meta_info.json").write_text(
                    json.dumps(loaded_meta.content, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
            except DraftCryptoError:
                pass
        script = draft.DraftFolder(str(stage_root)).load_template(new_draft_name)
        for spec in replacements or []:
            track_type = spec.get("track_type", "video")
            if track_type not in {"video", "audio"}:
                raise ManifestError("素材替换只支持 video 或 audio")
            track = script.get_imported_track(
                _enum(draft.TrackType, track_type, "替换轨道类型"),
                name=spec.get("track_name"),
                index=spec.get("track_index"),
            )
            source_path = Path(spec["source"]).expanduser().resolve()
            if not source_path.is_file():
                raise FileNotFoundError(source_path)
            material = draft.VideoMaterial(str(source_path)) if track_type == "video" else draft.AudioMaterial(str(source_path))
            source_range = _source_timerange(spec)
            script.replace_material_by_seg(track, int(spec["segment_index"]), material, source_range)
        for spec in text_replacements or []:
            track = script.get_imported_track(
                draft.TrackType.text,
                name=spec.get("track_name"),
                index=spec.get("track_index"),
            )
            script.replace_text(
                track,
                int(spec["segment_index"]),
                spec["text"],
                recalc_style=bool(spec.get("recalc_style", True)),
            )
        script.save()
        _update_meta_name(staged, new_draft_name)
        summary = _validate_content(staged)
        target = _child(settings.draft_root, new_draft_name)
        if not dry_run:
            _publish(staged, target)
        return {
            "success": True,
            "mode": "reference_blueprint_copy",
            "reference_draft": reference_draft,
            "source_format": source_format,
            "draft_name": new_draft_name,
            "draft_path": None if dry_run else str(target),
            "dry_run": dry_run,
            "summary": summary,
            "compatibility": compatibility_notice(settings),
            "stages": ["读取参考草稿", "复制剪辑蓝图", "替换素材与文本", "校验新草稿", "发布新草稿" if not dry_run else "完成试运行"],
        }
    finally:
        if stage_root.exists():
            shutil.rmtree(stage_root)


def batch_from_template(settings: Settings, template_draft: str, jobs: list[dict[str, Any]], *, dry_run: bool = False) -> dict[str, Any]:
    if not jobs:
        raise ManifestError("jobs 不能为空")
    names = [_safe_name(str(job.get("name", "")), "批量草稿名称") for job in jobs]
    if len(set(names)) != len(names):
        raise ManifestError("批量任务的草稿名称不能重复")
    results = []
    for job, name in zip(jobs, names):
        results.append(
            build_from_reference(
                settings,
                template_draft,
                name,
                replacements=job.get("replacements"),
                text_replacements=job.get("text_replacements"),
                dry_run=dry_run,
            )
        )
    return {
        "success": True,
        "mode": "template_batch",
        "template_draft": template_draft,
        "count": len(results),
        "results": results,
        "compatibility": compatibility_notice(settings),
    }
