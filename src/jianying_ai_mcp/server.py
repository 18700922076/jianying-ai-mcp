from __future__ import annotations

import os
from typing import Any, Awaitable, Callable

from mcp.server.fastmcp import Context, FastMCP

from .capabilities import capability_report
from .config import Settings, compatibility_notice
from .draft_ops import (
    analyze_reference_draft as analyze_reference,
    batch_from_template as run_template_batch,
    build_from_reference as run_reference_build,
    build_manifest,
)
from .draft_crypto import crypto_status
from .video_observer import analyze_video, get_analysis_context, observer_status, save_observations


mcp = FastMCP(
    "JianyingAIEditor",
    instructions=(
        "面向剪映的批处理草稿引擎。先调用 get_jianying_capabilities；"
        "每次执行必须向用户转述 compatibility.message。"
        "新建复杂视频优先一次调用 build_draft；复刻结构用 build_from_reference；"
        "批量换料用 batch_from_template。需要理解视频时先调用 analyze_video_source，"
        "读取代表帧后调用 save_video_observations。默认先 dry_run，用户确认后再正式发布。"
    ),
)


async def _stage(ctx: Context, current: int, total: int, title: str) -> None:
    await ctx.info(f"[阶段] {title}")
    await ctx.report_progress(current, total, title)


async def _execute(
    ctx: Context,
    titles: list[str],
    operation: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    await _stage(ctx, 0, len(titles), titles[0])
    try:
        result = operation()
    except Exception as exc:
        await ctx.error(f"[失败] {type(exc).__name__}: {exc}")
        return {
            "success": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "compatibility": compatibility_notice(Settings.from_env()),
        }
    for index, title in enumerate(titles[1:], start=1):
        await _stage(ctx, index, len(titles), title)
    return result


@mcp.tool()
async def get_jianying_capabilities(ctx: Context) -> dict[str, Any]:
    """返回已验证、禁用和首版未纳入的能力，并检测本机剪映版本。规划前必须调用。"""
    settings = Settings.from_env()
    await _stage(ctx, 1, 1, "读取能力与版本")
    return {
        "success": True,
        "capabilities": capability_report(settings),
        "compatibility": compatibility_notice(settings),
        "paths": {
            "draft_root": str(settings.draft_root),
            "work_root": str(settings.work_root),
            "jianying_exe": str(settings.jianying_exe) if settings.jianying_exe else None,
        },
        "video_observer": observer_status(settings),
        "encrypted_draft_reader": crypto_status(settings),
        "recommended_modes": ["creative", "strict", "reference"],
    }


@mcp.tool()
async def analyze_video_source(
    source: str,
    ctx: Context,
    detail: str = "efficient",
    start: str | float | int | None = None,
    end: str | float | int | None = None,
    max_frames: int | None = None,
    resolution: int = 512,
    subtitle_path: str | None = None,
    transcription: str = "auto",
) -> dict[str, Any]:
    """准备可供多模态 Agent 理解的视频上下文。

    返回视频元数据、语句级时间戳字幕、SRT 路径和代表帧绝对路径。detail 可选
    transcript/efficient/balanced/dense；默认 efficient。先粗看，再用 start/end 对候选区间复查。
    transcription 可选 auto/none/groq/openai；仅在没有字幕且配置 API Key 时上传压缩音频，
    不上传视频或画面。此工具负责提取，不会自行声称已看懂画面。
    """
    settings = Settings.from_env()
    return await _execute(
        ctx,
        ["检查视频与依赖", "提取字幕和代表帧", "生成视频观察报告"],
        lambda: analyze_video(
            settings,
            source,
            detail=detail,
            start=start,
            end=end,
            max_frames=max_frames,
            resolution=resolution,
            subtitle_path=subtitle_path,
            transcription=transcription,
        ),
    )


@mcp.tool()
async def save_video_observations(
    analysis_id: str,
    observations: list[dict[str, Any]],
    ctx: Context,
    edit_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """保存 Agent 对代表帧的结构化理解，供后续剪辑规划复用。

    observations 每项为 {time_ms, description, tags?, confidence?}；edit_candidates 每项为
    {start_ms, end_ms, reason, confidence?}。只保存模型已经实际读取过的画面结论。
    """
    settings = Settings.from_env()
    return await _execute(
        ctx,
        ["校验画面观察", "保存语义与候选片段"],
        lambda: save_observations(settings, analysis_id, observations, edit_candidates),
    )


@mcp.tool()
async def get_video_analysis_context(
    analysis_id: str,
    ctx: Context,
    start_ms: int | None = None,
    end_ms: int | None = None,
    offset: int = 0,
    limit: int = 120,
) -> dict[str, Any]:
    """按时间范围和分页读取已缓存的视频字幕、代表帧及观察路径。

    长视频不要一次加载完整字幕；从 offset=0 开始，根据 next_offset 继续。指定
    start_ms/end_ms 可只取得候选区间，避免重复抽帧和浪费上下文。
    """
    settings = Settings.from_env()
    return await _execute(
        ctx,
        ["读取缓存视频报告", "筛选时间范围与字幕页"],
        lambda: get_analysis_context(
            settings,
            analysis_id,
            start_ms=start_ms,
            end_ms=end_ms,
            offset=offset,
            limit=limit,
        ),
    )


@mcp.tool()
async def analyze_reference_draft(
    draft_name: str,
    ctx: Context,
    include_text: bool = False,
) -> dict[str, Any]:
    """读取参考草稿并提取剪辑蓝图；已配置读取器时支持剪映加密草稿且不修改源文件。"""
    settings = Settings.from_env()
    return await _execute(
        ctx,
        ["读取参考草稿", "提取轨道与节奏", "保存剪辑蓝图"],
        lambda: analyze_reference(settings, draft_name, include_text=include_text),
    )


@mcp.tool()
async def build_draft(
    manifest: dict[str, Any],
    ctx: Context,
    dry_run: bool = True,
) -> dict[str, Any]:
    """一次提交紧凑编辑清单并生成新草稿。

    manifest 结构：
    - draft: {name, width?, height?, fps?}
    - tracks: [{type, name, segments:[...]}]
    - srt_imports?: [{source, track_name?, style?, clip?}]

    轨道 type 支持 video/audio/text/sticker/effect/filter。时间字段使用 start、duration；
    媒体可加 source_start/source_duration、speed。具体字段见 Skill 的 manifest-schema.md。
    默认 dry_run=true，只在暂存区完整构建和校验；确认后传 false 发布到剪映草稿目录。
    """
    settings = Settings.from_env()
    return await _execute(
        ctx,
        ["校验编辑清单", "构建暂存草稿", "校验草稿结构", "发布草稿" if not dry_run else "完成试运行"],
        lambda: build_manifest(settings, manifest, dry_run=dry_run),
    )


@mcp.tool()
async def build_from_reference(
    reference_draft: str,
    new_draft_name: str,
    ctx: Context,
    replacements: list[dict[str, Any]] | None = None,
    text_replacements: list[dict[str, Any]] | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """复制明文或可解密参考草稿的剪辑蓝图，并按轨道与片段下标替换素材或文本。

    replacements 项：{track_type, track_name? 或 track_index?, segment_index, source,
    source_start?, source_duration?}。text_replacements 项：{track_name? 或 track_index?,
    segment_index, text, recalc_style?}。
    """
    settings = Settings.from_env()
    return await _execute(
        ctx,
        ["读取参考草稿", "复制剪辑蓝图", "替换素材与文本", "校验新草稿", "发布新草稿" if not dry_run else "完成试运行"],
        lambda: run_reference_build(
            settings,
            reference_draft,
            new_draft_name,
            replacements,
            text_replacements,
            dry_run=dry_run,
        ),
    )


@mcp.tool()
async def batch_from_template(
    template_draft: str,
    jobs: list[dict[str, Any]],
    ctx: Context,
    dry_run: bool = True,
) -> dict[str, Any]:
    """基于一个明文模板批量生成草稿。每个 job 包含 name、replacements、text_replacements。"""
    settings = Settings.from_env()
    return await _execute(
        ctx,
        ["校验批量任务", "逐份复制与替换", "校验批量草稿", "发布批量结果" if not dry_run else "完成批量试运行"],
        lambda: run_template_batch(settings, template_draft, jobs, dry_run=dry_run),
    )


def main() -> None:
    transport = os.environ.get("JIANYING_MCP_TRANSPORT", "stdio")
    if transport not in {"stdio", "sse", "streamable-http"}:
        raise ValueError(f"不支持的 JIANYING_MCP_TRANSPORT: {transport}")
    mcp.run(transport=transport)
