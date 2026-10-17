---
name: jianying-ai-editor
description: Understand local or URL videos through timestamped transcripts and representative frames, then plan natural-language edits and safely create, inspect, imitate, or batch-produce Jianying/CapCut China desktop drafts through the Jianying AI MCP. Use for 视频内容识别, 参考成片学习, 长视频选段, 剪映草稿, 自动剪辑, 字幕排版, 音画对齐, 变速, 配乐, 转场, 动画, 特效, 参考已有草稿制作类似视频, or template-based batch replacement.
---

# 剪映 AI 剪辑

把自然语言需求转换为紧凑编辑清单，再交给批处理 MCP 一次生成。不要把每个片段拆成一次工具调用。

## 首先选择工作模式

需求未明确时，引导用户选择并补充最少必要信息：

1. `自动补全创作`：允许补足镜头节奏、字幕样式、转场、配乐和缺失细节。
2. `严格按照要求`：不增加未授权创作决策；影响成片的歧义先询问。
3. `参考草稿`：学习或复制指定草稿的轨道、节奏和样式；询问保留项、替换项及允许变化程度。
4. `参考成片`：观察导出视频的字幕、镜头和内容结构；询问重点学习内容、允许相似程度和是否需要逐段复查。

收集：成片目的、画幅/时长、素材路径、字幕语言、配乐方向、不可修改项、是否允许在线或会员资源、输出草稿名称。已有信息不要重复询问。

## 固定工作流

1. 调用 `get_jianying_capabilities`。
2. 每次都向用户转述 `compatibility.message`。版本不一致时必须说明动画、在线资源、滤镜和草稿结构可能不同，并要求生成后抽样确认。
3. 给出 3–7 个简短阶段标题，让用户知道正在规划、校验、生成或发布。
4. 选择工具：
   - 理解视频或参考成片：`analyze_video_source`，读取返回的代表帧后调用 `save_video_observations`
   - 从零生成：`build_draft`
   - 学习参考：`analyze_reference_draft`
   - 保持参考结构并换料：`build_from_reference`
   - 多份同版式：`batch_from_template`
5. 默认先 `dry_run=true`。向用户概括轨道、时长、替换数量和兼容风险；用户已明确授权正式生成时再用 `dry_run=false`。
6. 发布后要求刷新或重启剪映再打开；不要在 `.locked` 存在时覆盖草稿。

## 视频观察工作流

需要理解画面时读取 [video-observer.md](references/video-observer.md)，并严格区分“已提取”和“已观察”。

1. 默认用 `detail=efficient`、`max_frames=24–40` 粗看；纯字幕任务用 `transcript`。
2. 实际读取工具返回的每张代表帧。未读取图片时不得声称理解画面。
3. 把画面事实和候选片段写入 `save_video_observations`；描述应包含时间、人物/主体、动作、场景、可读文字和不确定性。
4. 对快动作、字幕小字、镜头遗漏或关键候选区间，用 `start/end` 局部重跑；只在必要时使用 `balanced/dense` 或 1024px。
5. 将观察报告转成剪辑清单。视频片段的 `source_start/source_duration` 使用候选区间，字幕可直接复用返回的 `transcript_srt_path`。
6. 同一输入和参数会命中缓存。后续提问优先复用 `analysis_id` 和报告，不重复抽帧。
7. `transcript_truncated=true` 时使用 `get_video_analysis_context` 分页或按时间范围读取，不要要求 MCP 一次返回全部长字幕。

## 参考草稿模式

先调用 `analyze_reference_draft`，把结果称为“剪辑蓝图”，不要声称理解了无法读取的画面语义。

- 用户要求高度一致：使用 `build_from_reference` 或 `batch_from_template`，保留时间轴和资源引用。
- 用户只要求相似节奏：读取蓝图后生成新的 manifest，复用片段长度分布、轨道层级、字幕位置和代表性动画。
- 草稿已加密：读取 [encrypted-drafts.md](references/encrypted-drafts.md)。只有 `encrypted_draft_reader.available=true` 时继续；只处理临时副本，绝不改写参考草稿。读取器不可用时说明缺少的本地依赖，不要谎称草稿不可读。

## 能力约束

只把 `verified` 能力作为稳定功能。遇到蒙版、模糊背景、音量关键帧、剪映内置 TTS/ASR/卡点或自动导出时，先读取 [capabilities.md](references/capabilities.md)，说明降级方案，未经用户同意不要扩大依赖。视频理解属于“代表帧＋字幕”的部分能力，不要表述为逐帧完整观看。

构建或修改 manifest 时读取 [manifest-schema.md](references/manifest-schema.md)。配置 OpenCode、DeepSeek Harness 或其他客户端时读取 [client-config.md](references/client-config.md)。

## 安全与效率

- 新草稿默认不覆盖同名草稿；不要删除或修改用户其他草稿。
- 只接受绝对素材路径；发布前校验素材存在、时间范围、轨道重叠和草稿 JSON。
- 色度抠图颜色必须是 `#RRGGBBAA`。
- 同一音频片段每类音效最多一个。
- 优先返回概要和保存路径，不把完整草稿 JSON 塞回模型上下文。
- 对批量任务先用一个样本试运行，再批量发布。
