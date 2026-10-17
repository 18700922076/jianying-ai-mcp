# 紧凑编辑清单

## 最小示例

```json
{
  "draft": {"name": "产品短片_01", "width": 1920, "height": 1080, "fps": 30},
  "tracks": [
    {
      "type": "video",
      "name": "主画面",
      "segments": [
        {
          "source": "<ABSOLUTE_VIDEO_PATH>",
          "start": "0s",
          "duration": "3s",
          "source_start": "1s",
          "source_duration": "3s",
          "speed": 1.0,
          "transition": {"name": "叠化", "duration": "0.4s"}
        }
      ]
    },
    {
      "type": "text",
      "name": "标题",
      "segments": [
        {
          "text": "示例标题",
          "start": "0s",
          "duration": "2s",
          "style": {"size": 8, "bold": true, "align": 1},
          "border": {"color": [0, 0, 0], "width": 20},
          "clip": {"transform_y": 0.65}
        }
      ]
    }
  ]
}
```

## 公共字段

- 时间使用微秒整数或 `0.5s`、`2s` 等字符串。
- 所有片段：`start`、`duration`。
- 媒体片段：`source` 绝对路径；可选 `source_start`、`source_duration`、`speed`、`volume`、`change_pitch`。
- `clip`：`alpha`、`rotation`、`scale_x/y`、`transform_x/y`、`flip_horizontal/vertical`。

## 视频或图片片段

- `transition`: `{name, duration?}`
- `filter`: `{name, intensity?}`
- `effects`: `[{name, params?}]`
- `mix_mode`: 如 `滤色`
- `chroma`: `{color: "#00FF00FF", intensity?, edge_smooth?, spill?}`
- `animations`: `[{category: video_intro|video_outro|video_group, name, duration?}]`
- `keyframes`: `[{property: position_x|position_y|uniform_scale|rotation|alpha, time, value}]`

图片也放在 `video` 轨，`source` 指向本地图片。

## 音频片段

- `fade`: `{in, out}`
- `effects`: `[{category: scene|tone|speech_to_song, name, params?}]`
- 首版拒绝音量关键帧。

## 文本片段

- `text` 必填；可选 `font`。
- `style`: `size`、`bold`、`italic`、`underline`、`color`、`alpha`、`align`、`vertical`、`letter_spacing`、`line_spacing`、`auto_wrapping`、`max_line_width`。
- `border`、`background`、`shadow` 对应文本描边、背景和阴影。
- `bubble`: `{effect_id, resource_id}`；`flower_text_resource_id` 为资源 ID。
- `animations`: category 使用 `text_intro`、`text_outro`、`text_loop`。
- `keyframes` 支持位置、缩放、旋转和透明度。

## 贴纸、独立特效和独立滤镜

- 贴纸轨：`{resource_id, start, duration, clip?, keyframes?}`。
- effect 轨：`{name, start, duration, params?}`。
- filter 轨：`{name, start, duration, intensity?}`。

## SRT

顶层加入：

```json
{
  "srt_imports": [
    {
      "source": "<ABSOLUTE_SUBTITLE_PATH>",
      "track_name": "字幕",
      "time_offset": 0,
      "style": {"size": 5, "align": 1, "auto_wrapping": true},
      "clip": {"transform_y": -0.8}
    }
  ]
}
```

## 参考草稿替换

素材替换项：

```json
{
  "track_type": "video",
  "track_name": "主画面",
  "segment_index": 0,
  "source": "<ABSOLUTE_REPLACEMENT_PATH>"
}
```

文字替换项：

```json
{"track_name": "标题", "segment_index": 0, "text": "新标题", "recalc_style": true}
```

不知道轨道名时可传同类型 `track_index`。先调用 `analyze_reference_draft` 获取轨道和片段下标。
