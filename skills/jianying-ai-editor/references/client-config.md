# MCP 客户端配置

服务使用标准 `stdio` MCP，不依赖特定模型。Codex、OpenCode、DeepSeek Harness 或其他客户端只要支持本地 stdio MCP 即可调用。

## 通用配置要素

```json
{
  "command": "<PYTHON_EXECUTABLE>",
  "args": ["-m", "jianying_ai_mcp"],
  "env": {
    "JIANYING_DRAFT_ROOT": "<JIANYING_DRAFT_ROOT>",
    "JIANYING_MCP_WORK_ROOT": "<WORK_ROOT>",
    "JIANYING_EXE": "<JIANYING_EXECUTABLE>",
    "JIANYING_FFMPEG": "<FFMPEG_EXECUTABLE>",
    "JIANYING_FFPROBE": "<FFPROBE_EXECUTABLE>"
  }
}
```

不同客户端外层键名可能是 `mcpServers`、`mcp` 或客户端专用的 local-server 配置。保持 `command`、`args`、`env` 三部分不变，并优先参考该客户端当前版本文档。

## 运行约束

- 工作目录不重要，包以 editable 模式安装在项目虚拟环境中。
- stdio 模式下不要把普通日志写到 stdout；服务的阶段消息通过 MCP logging/progress 发送。
- DeepSeek 只是推理模型时，由承载它的 Harness 负责 MCP 工具调用；模型 API 本身不直接启动本地进程。
- 视频观察需要支持本地图片读取的多模态客户端。纯文本模型只能使用字幕和其他视觉模型保存的观察结果。
- URL 视频另设 `JIANYING_YT_DLP`；无字幕转写可选设 `GROQ_API_KEY` 或 `OPENAI_API_KEY`。API Key 不应写进可提交的公共配置。
- 客户端若不显示 progress，最终工具结果仍返回 `stages`。
