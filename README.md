# 剪映 AI 编辑 MCP + Skill

自然语言剪辑规划、视频观察与草稿执行相互分离：视频观察器负责本地元数据、字幕和代表帧；Skill 负责需求引导、画面理解与紧凑编辑清单；MCP 负责确定性生成、参考草稿复用、批量换料、安全校验和本地阶段进度。

验证基线：剪映专业版 `10.0.5.13816`、pyJianYingDraft `0.3.0`、MCP Python SDK `1.29.0`。

## 目录

- `src/jianying_ai_mcp/`：批处理 MCP 服务
- `skills/jianying-ai-editor/`：剪映草稿编辑 Skill
- `skills/food-visit-edit/`：横屏美食探店成片 Skill。参考片先用 Hypit 读，再按六拍剪进剪映
- `docs/capability_matrix.md`：完整测试结论
- `configs/`：客户端配置样例
- `tests/`：从零生成、参考复用、批量换料及安全边界测试

## MCP 工具

- `get_jianying_capabilities`：版本、能力与限制
- `analyze_reference_draft`：从明文或本机可解密草稿提取剪辑蓝图
- `build_draft`：一次提交编辑清单，从零生成草稿
- `build_from_reference`：复制蓝图并按轨道/片段换料换字
- `batch_from_template`：一个模板批量生成多份草稿
- `analyze_video_source`：提取带时间戳字幕和代表帧，支持缓存与局部复查
- `save_video_observations`：保存 Agent 实际读取代表帧后的结构化理解
- `get_video_analysis_context`：按时间范围和页码读取长字幕与缓存上下文

工具默认 `dry_run=true`，会在项目暂存区完整生成和校验，但不发布到剪映目录。正式发布不会覆盖同名草稿。

## 启动

Python 3.12：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

需要分析在线视频时改用 `-e ".[video-url]"`。本地视频只需要系统已有的 ffmpeg/ffprobe，不需要 yt-dlp。

Skill 可从仓库安装到支持 Agent Skills 的客户端：

```powershell
npx skills add . -g
```

然后按客户端使用 [MCP 配置样例](configs/mcp-stdio.example.json)。

启动服务：

```powershell
.\.venv\Scripts\python.exe -m jianying_ai_mcp
```

服务使用 stdio MCP；普通日志不写 stdout。客户端支持时会显示 `[阶段]` 日志和 progress，其他客户端仍可从返回值的 `stages` 查看阶段。

## 环境变量

- `JIANYING_DRAFT_ROOT`：剪映草稿根目录
- `JIANYING_MCP_WORK_ROOT`：暂存和参考蓝图目录
- `JIANYING_EXE`：剪映程序路径，用于检测版本
- `JIANYING_DRAFT_CRYPTO`：可选的 `jy-draftc.exe` 兼容读取器；启用加密草稿学习/复制
- `JIANYING_MCP_TRANSPORT`：默认 `stdio`，也可使用 `sse` 或 `streamable-http`
- `JIANYING_FFMPEG` / `JIANYING_FFPROBE`：视频观察器使用的可执行文件
- `JIANYING_YT_DLP`：可选，仅 URL 输入需要
- `GROQ_API_KEY` / `OPENAI_API_KEY`：可选，仅无字幕时的 Whisper 转写需要

ffmpeg/ffprobe 可从 `PATH` 自动发现，也可通过环境变量显式配置。视频和画面始终留在本机；只有无字幕且启用 Whisper API 时会上传压缩音频。

## 加密草稿读取

剪映保存后的 `draft_content.json` 可能是加密文本。0.3 通过已审计的 MIT 开源 `jy-draftc` 接口调用本机剪映目录中的 `videoeditor.dll`，支持分析、参考复制和批量换料。读取时会先复制到一次性工作目录，解密结果用完即删，绝不原地改写参考草稿。

仓库包含最小读取器源码 [native/jy_draft_crypto.cpp](native/jy_draft_crypto.cpp)，可用 MinGW-w64 执行 `g++ -std=c++17 -O2 -municode -static -o jy-draftc.exe native/jy_draft_crypto.cpp` 构建。随后配置 `JIANYING_DRAFT_CRYPTO`；`JIANYING_EXE` 必须指向同一套剪映安装。先调用 `get_jianying_capabilities`，确认 `encrypted_draft_reader.available=true` 再读取加密草稿。

## 视频理解

推荐先调用 `analyze_video_source(detail="efficient", max_frames=24~40)` 粗看。Agent 必须实际读取返回的代表帧后，才能调用 `save_video_observations` 声称理解画面。长视频先读字幕定位，再对候选区间用 `start/end` 局部复查，避免一次输入大量图片。

当前为语句级字幕时间戳，不是逐字强制对齐；代表帧也不是完整逐帧视频流。快动作、口型级同步、音乐节拍仍需专用分析器。

配置格式见 [OpenCode 配置](configs/opencode.example.json) 和 [通用 stdio 配置](configs/mcp-stdio.example.json)。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试前可使用 [测试与反馈清单](docs/testing_guide.md)。

## 当前边界

当前不自动操作剪映内置音乐/音效库、TTS、节拍卡点和自动导出。独立视频观察器已支持旁挂字幕及可选 Whisper 语句级转写，但不等同于剪映内置字幕识别。蒙版、模糊背景及音量关键帧因当前版本实测不稳定而被显式拒绝。参考草稿支持明文 JSON；配置本地读取器后也支持有权访问的剪映加密草稿。

第三方许可说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

每次工具调用都返回版本兼容提示。若剪映版本与 `10.0.5.13816` 不同，客户端必须向用户说明预设、在线资源和草稿结构可能变化，并先生成小样抽检。
