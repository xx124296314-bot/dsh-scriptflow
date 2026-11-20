# 更新记录

## 0.2.0 — 2026-11-20

- `sf_run` 的 `url` 支持多行：一行一条链接，一次跑多条，每条各出一份 mp4 + Word（顺序执行，单条失败不影响其他条）。
- 新增 `limit` 参数，批量时限制最多处理前 N 条。
- 批量任务的结果里每条都带 `ok / title / author / paragraphs / videoPath / docxPath`；`sf_job` 的 `status.json` 增加 `itemIndex / itemTotal / items`，进度条按整批折算。
- 同名交付文件不再互相覆盖：重复标题自动用 `标题 (2).docx` 这样的后缀。
- 校对改进：跨段断句会被拼回去；字数比例护栏放宽到 0.70–1.20，短文本里加书名号不再被误判丢弃。

## 0.1.0 — 2026-11-20

- 首个版本：`sf_run` / `sf_job` / `sf_settings` 三个工具。
- 链路：HTTP 下载无水印 mp4 → ffmpeg 抽 16 kHz 单声道 → 本地 SenseVoice int8 识别 → 本地规则清洗 → DeepSeek 校对 → 写纯文字 docx → 删除中间音轨。
