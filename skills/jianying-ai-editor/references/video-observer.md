# 视频观察与剪辑规划

## 选择分析档位

| 任务 | 初次参数 | 复查策略 |
|---|---|---|
| 字幕整理、内容摘要 | `transcript` | 无画面问题时不抽帧 |
| 通用剪辑、长视频选段 | `efficient`, 24–40帧 | 对候选区间使用 `balanced` |
| 参考成片、镜头结构 | `balanced`, 40–80帧 | 对快速或细节片段局部 `dense` |
| 小字、界面操作 | 先512px | 仅相关区间升至1024px |

不要把 `dense` 作为默认值。长视频先依靠字幕定位，再局部观察。

## 工具输出

`analyze_video_source` 返回：

- `analysis_id`：缓存和保存观察结论的标识。
- `metadata`：时长、分辨率、编码、音轨状态。
- `frames`：`time_ms`、本地绝对路径和选择原因。
- `transcript`：语句级 `start_ms/end_ms/text`。
- `transcript_srt_path`：可直接导入剪映草稿的 SRT。
- `report_path`：完整本地 JSON 报告。

初次返回最多携带120个字幕片段。若 `transcript_truncated=true`，调用 `get_video_analysis_context`，使用 `offset/limit` 分页；已定位区间时优先传 `start_ms/end_ms`。

本地视频必须使用绝对路径。URL 需要配置 `yt-dlp`。旁挂 `.srt/.vtt` 优先；没有字幕时，`transcription=auto` 只会在存在 Groq/OpenAI Key 时调用 Whisper。

## 观察代表帧

按时间顺序实际打开所有返回帧，再生成：

```json
{
  "observations": [
    {
      "time_ms": 12500,
      "description": "中景，人物面向镜头讲话，背景保持不变",
      "tags": ["人物", "口播", "中景"],
      "confidence": 0.9
    }
  ],
  "edit_candidates": [
    {
      "start_ms": 11000,
      "end_ms": 18500,
      "reason": "句意完整且画面稳定，适合作为成片开场",
      "confidence": 0.82
    }
  ]
}
```

只写可见事实。代表帧之间的动作连续性属于推断，标低置信度；需要时局部重跑。

## 转成剪映清单

- 候选区间映射为视频片段的 `source_start/source_duration`。
- 未裁切整条源视频时可直接使用 `transcript_srt_path`。重排候选区间时必须重映射字幕时间；逐字高亮、口型级对齐需要额外强制对齐器。
- 场景变化可作为切点候选，不等同于最终艺术判断。
- 节拍卡点、波形同步和响度关键帧不由视频观察器完成。

## 隐私和模型兼容

- 视频和代表帧留在本机；无字幕且启用 Whisper API 时只上传压缩音频。
- 支持图片输入的 Codex/OpenCode 模型可直接读取帧。
- 纯文本 DeepSeek Harness 只能使用字幕和已有画面描述；应配置视觉模型旁路，保存观察后再交给文本模型规划。
- 不在 MCP 返回中重复嵌入所有图片；复用本地路径与缓存，控制 Token。
