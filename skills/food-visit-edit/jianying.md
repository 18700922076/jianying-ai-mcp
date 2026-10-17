# 剪映草稿发布和导出

剪映 10.9，程序 `E:/剪影/JianyingPro/JianyingPro.exe`。草稿根目录：

`C:/Users/lulu/AppData/Local/JianyingPro/User Data/Projects/com.lveditor.draft`

目录清单：同目录下的 `root_meta_info.json`。布局：`C:/Users/lulu/AppData/Local/JianyingPro/User Data/Config/draftLayoutConfig.ini`。

组装器用 `E:/memory/tools/jianying-ai-mcp`（venv 的 Python 3.13，pyJianYingDraft 0.3）。环境变量：`JIANYING_DRAFT_ROOT`、`JIANYING_MCP_WORK_ROOT`、`JIANYING_EXE`、`JIANYING_FFMPEG`。

## 发布

1. 先退出剪映。工程还开着，或目录里有 `.locked`，不要替换。
2. 目标文件夹必须是一次全新拷贝。组装器发现文件夹已存在会拒绝写入。不要把新稿合并进残留目录。
3. `.backup` 经常在剪映退出后仍拒绝访问。`Move-Item` 会移走正文、留下 `.backup`，目录就裂开。处理：`takeown /F .backup /R /D Y`，`icacls` 给当前用户 `(OI)(CI)F /T`，再 `rmdir /s /q .backup`，然后删掉父目录。损坏的副本移出草稿根目录，避免剪映扫到。
4. 发布成功后，生成器模板里的 id 是别人的工程（正文 id `91E08AC5-22FB-47e2-9AA0-7DC300FAEA2B`，meta id `BC69C7CD-7C5E-4185-B284-AF3E1047A664`，后者属于「主卧布局介绍」）。必须改成同一个新 UUID，写进：
   - `draft_content.json` 的 content id
   - `draft_meta_info.json` 的 `draft_id`、`draft_name`、`draft_fold_path`、`draft_root_path`、`tm_duration`（微秒）、`draft_new_version` `164.0.0`
   - `root_meta_info.json` 里路径以这个草稿名结尾的那条。`draft_json_file` 在 `draft_content.json` 前只用一个反斜杠。清掉 `draft_cover`
   - `draftLayoutConfig.ini` 里该草稿的 `oldId=0`
5. 三套 id 不一致时，剪映会显示空名称、时长 0。已经登记过的目录里再塞一份新正文，也会坏。生成器写出的是明文 JSON；剪映正常打开后会加密，并在第一次干净打开时重写 id。

## 导出

`JianyingController.export_draft` 只支持剪映 6 及以下。10.9 的窗口类名含 `MainWindow`，它会去找不存在的 `TitleBarButton`，一直等到超时。不要用它。

窗口是 QML（`Qt622QWindowIcon`，标题「剪映专业版」），UIA 树几乎是空的。`SetActive` / `SendKeys` 会卡住。不要用。

可用的做法：

1. 在首页双击该草稿（封面可能是黑的）。确认时间线已经打开，标题栏有青色「导出」。
2. `EnumWindows` 找到「剪映专业版」，`PrintWindow` 截图，在标题栏右侧找青色按钮。每次都重新量窗口位置。点击坐标和 `GetWindowRect` 用同一套坐标，避免 DPI 点到「分享」或「绑定账号」。
3. 弹出的窗口标题以 `导出-` 开头。确认为 1080P、MP4、30fps。
4. 底部按钮必须是「导出」。若是「开通会员并导出」，关掉对话框，找到会员素材（通常是人声上的场景特效），删掉后重新发布。不要点开通会员，不要点「开通 AI Ultra」。
5. 导出成功的界面有「发布」。用户没说要在剪映里发抖音，就不要点发布。
6. 对话框里的路径可能落在 `E:/memory/装修/`。用 ffmpeg 读真实文件，核对时长和音轨后再告诉用户。能改保存位置时，优先 `data/<店>/export/<草稿名>.mp4`。
