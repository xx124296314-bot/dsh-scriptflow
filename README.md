# 文案直出 ScriptFlow

**一条抖音链接 → 无水印 mp4 + 纯文字 Word。**

这是给 DeepSeek Harness 用的 Host-only 插件：不需要网页、不需要服务端口，由 Agent 直接调三个工具跑完整条链路。下载、抽音轨、语音识别（本地 SenseVoice，0 元）、DeepSeek 校对、写 docx，全程不弹窗、不占屏、不留音频。

## 安装

```
# 1. 把仓库放到本机任意目录（link 安装，不在 profile 里复制一份）
git clone https://github.com/xx124296314-bot/dsh-scriptflow.git E:\Tools\dsh-scriptflow

# 2. 注册进 DeepSeek Harness
plugin_manager install_bundle target=E:\Tools\dsh-scriptflow
```

装好后工具面板会多出 `sf_run` / `sf_job` / `sf_settings`。插件以 `link:` 方式挂到 profile，改完源码重载即可生效。

## 它依赖什么

插件本身零 npm 依赖，真正的活由本机的 shortvideo 引擎 + ffmpeg + sherpa-onnx 干：

| 依赖 | 作用 | 参考环境（本机） |
| --- | --- | --- |
| DeepSeek Harness（桌面版） | 宿主，提供 `tool` 与 `plugin_manager` | 任意近期版本 |
| Python 3.10+ | 跑 `engine/run.py` | `E:\Tools\sv-engine\Scripts\python.exe` |
| `python-docx`、`numpy` | 写 Word、音频处理 | 同上 venv |
| `sherpa-onnx` | 本地 SenseVoice 识别 | 2.0.x |
| shortvideo 引擎 | 下载无水印视频 + 抽音轨 + 识别 | `E:\Tools\dsh-shortvideo-export\engine` |
| SenseVoice 模型 | 228 MB，首次要下 | `E:\Tools\shortvideo-export-home\models\...` |
| `ffmpeg` / `ffprobe` | 抽 16 kHz 单声道 | 在 PATH 上 |
| DeepSeek API Key（可选） | 校对；不填只走本地规则 | — |

换机器时把这几个路径用 `sf_settings` 指过去即可，见下。

## 第一次使用

```
# 看当前配置（默认值、模型、引擎）
sf_settings action=get

# 指路径（换成你自己的）
sf_settings action=set key=python          value=E:\Tools\sv-engine\Scripts\python.exe
sf_settings action=set key=engineDir       value=E:\Tools\dsh-shortvideo-export\engine
sf_settings action=set key=dataHome        value=E:\Tools\shortvideo-export-home

# 填 DeepSeek Key（校对用；不填就只走本地规则清洗）
sf_settings action=set key=deepseekKey     value=sk-xxxx
```

配置写在插件 home 的 `config.json`（默认 `E:/Tools/scriptflow-home/config.json`），不会进 git。

## 用法

单条，同步跑（几十秒的片子十几秒出结果）：

```
sf_run url=https://v.douyin.com/xxxxxx/
```

**批量**：`url` 里一行一条，一次跑多条（按顺序跑完，每条各出一份 mp4 + Word）：

```
sf_run url=https://v.douyin.com/aaaaaa/
https://v.douyin.com/bbbbbb/
7706703615318578789
```

批量想限制条数就加 `limit=10`；怕等太久就加 `background=true`，然后轮询：

```
sf_run url=<多行链接> background=true
sf_job action=status id=sfxxxx
```

取消：

```
sf_job action=cancel id=sfxxxx
```

批量结果里，每条都有独立的 `title / author / paragraphs / videoPath / docxPath`；单条失败不会中断整批，失败条目会在 `items` 里带 `ok:false` 和原因。

## 交付物

```
<home>/output/<作者>/<标题>.docx    纯正文：微软雅黑 12pt、1.5 倍行距、无标题无作者
<home>/output/<作者>/<标题>.mp4     无水印原片（作者自带的小水印属于原视频）
```

任务过程文件在 `<home>/jobs/<id>/`：`status.json`（进度）、`result.json`（结构化结果）、`cli.log`（引擎日志）。

## 配置项

| 键 | 说明 |
| --- | --- |
| `deepseekKey` / `deepseekBase` / `deepseekModel` | 校对用的大模型，默认 `https://api.deepseek.com` + `deepseek-chat` |
| `engine` | 默认识别引擎：`sensevoice`（本地 0 元，默认）/ `faster-whisper` / `dashscope`（百炼 0.06 元每小时）/ `volc`（火山 0.8 元每小时）/ `official`（仅小红书） |
| `outputDir` | 交付目录，默认 `<home>/output` |
| `python` | 跑引擎的 Python 解释器 |
| `engineDir` / `dataHome` | shortvideo 引擎目录与它的数据目录（素材库、模型） |

配置存在 `E:\Tools\scriptflow-home\config.json`，用 `sf_settings` 改，不要手改 profile。

## 校对是怎么做的

1. **本地规则**：补标点、合并被切开的词、术语表替换（库里的 `terms` 表）、跨段重复字拼接。
2. **DeepSeek**：把标题、作者、话题作为上下文，只允许改错字与断句；段数不许变多、总字数偏差超过 20% 直接丢弃这版结果；被切在段落中间的句子会拼回上一段。

大模型改不了「听错」：如果识别把一句歌词听成了别的词，校对不会替你编造，需要换云端识别或加术语表。音乐/重复歌词的片子，标点和分段偶尔还会不完美。

## 已知限制

- 只支持抖音（作品链接 / 分享口令 / 纯 ID）；小红书不在这一版。
- 批量是「顺序跑」：一次调用里多条排队跑完，不会并发出片；需要并发就开多个后台任务。
- 首次跑要下模型（228 MB），本机已就绪。
- 插件不上传任何文件；DeepSeek 只收到文字段落和标题/作者。
- 火山引擎需要公网可访问的音频 URL，本地任务会退回其他引擎。

## 许可与致谢

- 本仓库以 **MIT** 发布：可以自由使用、修改、二次分发，保留版权与许可声明即可。
- 下载与识别那一段调用的是本机已装的 [shortvideo 引擎](https://github.com/hangyesite/dsh-shortvideo-export)，本仓库不包含它的代码与模型文件。
- SenseVoice 模型由 [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) 提供，使用时遵守它自己的许可。
