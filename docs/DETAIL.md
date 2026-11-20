# ScriptFlow · 文案直出

一条抖音链接 → 无水印 mp4 + 纯文字 Word。插件在宿主机上调用本机的 shortvideo 引擎完成下载与语音识别，再用 DeepSeek 做校对，最后自己写 docx。

## 工具

| 工具 | 作用 |
| --- | --- |
| `sf_run` | 跑完整条链路。`background: true` 立即返回任务号，用 `sf_job` 轮询 |
| `sf_job` | `list` / `status` / `cancel` 后台任务 |
| `sf_settings` | 查看或修改配置（DeepSeek Key、识别引擎、输出目录、Python 解释器） |

## 链路

1. 纯 HTTP 拉流下载无水印视频（不需要浏览器窗口）
2. `ffmpeg` 抽 16 kHz 单声道音轨
3. 本地 SenseVoice int8 识别（CPU，约 13 倍实时；也可切 faster-whisper / 百炼 / 火山）
4. 本地规则清洗（补标点、合并被切开的词）→ DeepSeek 校对（同音错字、断句、按标题和话题修正专有名词）
5. 写 docx（微软雅黑 12pt、1.5 倍行距、只有正文）并删掉中间音轨

## 配置

配置保存在 `$DSH` 之外的插件 home 目录（默认 `E:/Tools/scriptflow-home/config.json`），字段：

- `deepseekKey`、`deepseekBase`、`deepseekModel`：校对用的大模型
- `engine`：默认识别引擎
- `outputDir`：交付目录
- `python`、`engine`（引擎目录）、`home`（引擎数据目录）

用 `sf_settings action=set` 修改，不需要改 profile 文件。

## 已知限制

- 只接受抖音（含分享口令、作品链接、纯 ID）；小红书和作者主页批量不在这一版范围内。
- 语音识别把词听错（例如把一句歌词听成「阳月亮」）时，校对不会替你编；要更准请换云端识别或加术语表。
- 首次识别需要 228 MB 的 SenseVoice 模型（本机已装好），换机器要重新下载。
- 视频与 docx 留在本机目录，插件不上传任何文件。
