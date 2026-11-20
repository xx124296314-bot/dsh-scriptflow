# 文案直出 ScriptFlow

**一条抖音链接 → 无水印 mp4 + 纯文字 Word。**

这是给 DeepSeek Harness 用的 Host-only 插件：不需要网页、不需要服务端口，由 Agent 直接调三个工具跑完整条链路。下载、抽音轨、语音识别（本地 SenseVoice，0 元）、DeepSeek 校对、写 docx，全程不弹窗、不占屏、不留音频。

## 安装

```
plugin_manager install_bundle target=E:\Tools\dsh-scriptflow
```

装好后工具面板会多出 `sf_run` / `sf_job` / `sf_settings`。插件以 `link:` 方式挂到 profile，改完源码重载即可生效。

## 第一次使用

1. 确认本机已装识别引擎依赖（本机现状）：
   - Python 环境 `E:\Tools\sv-engine\Scripts\python.exe`（python-docx、sherpa-onnx、numpy）
   - 识别引擎目录 `E:\Tools\dsh-shortvideo-export\engine`
   - SenseVoice 模型在 `E:\Tools\shortvideo-export-home\models\...`（228 MB）
   - ffmpeg 在 PATH
2. 填 DeepSeek Key（校对用；不填就只走本地规则清洗）：

   ```
   sf_settings action=set key=deepseekKey value=sk-xxxx
   ```

3. 看当前配置：

   ```
   sf_settings action=get
   ```

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
2. **DeepSeek**：把标题、作者、话题作为上下文，只允许改错字与断句；段数不许变多、总字数偏差超过 30% 直接丢弃这版结果。

大模型改不了「听错」：如果识别把一句歌词听成了别的词，校对不会替你编造，需要换云端识别或加术语表。音乐/重复歌词的片子，标点和分段偶尔还会不完美。

## 已知限制

- 只支持抖音（作品链接 / 分享口令 / 纯 ID）；小红书不在这一版。
- 批量是「顺序跑」：一次调用里多条排队跑完，不会并发出片；需要并发就开多个后台任务。
- 首次跑要下模型（228 MB），本机已就绪。
- 插件不上传任何文件；DeepSeek 只收到文字段落和标题/作者。
- 火山引擎需要公网可访问的音频 URL，本地任务会退回其他引擎。
