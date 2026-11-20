#!/usr/bin/env python
"""ScriptFlow engine driver.

Runs one Douyin link through the locally installed shortvideo engine and turns
the result into two deliverables:

  * a watermark-free mp4
  * a plain-text .docx (body text only: no title, no author, no timestamps)

The heavy lifting is delegated to `python -m shortvideo ...` (download +
transcription).  This driver adds the parts the CLI does not do for us:

  * live progress for the plugin (`status.json`)
  * paragraph building from the transcript segments
  * deterministic rule cleaning, then an optional DeepSeek proofread
  * docx writing and file delivery
  * deleting the intermediate audio track

It is deliberately dependency-light: stdlib + python-docx (already present in
the plugin's dedicated venv).  No network calls except the optional proofread.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent
MARKER = "===RESULT==="
STAGES = ("download", "audio", "asr", "proof", "export")
STAGE_ZH = {"download": "下载", "audio": "抽音轨", "asr": "语音识别", "proof": "校对", "export": "导出"}


# --------------------------------------------------------------------- utils

def now_iso() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def write_json(path: Path, payload) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def sanitize_name(text: str, limit: int = 40) -> str:
    text = re.sub(r"#\S+", "", text or "")
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", text)
    text = re.sub(r"\s+", " ", text).strip(" ._-")
    return (text[:limit].strip() or "未命名作品")


def humanize_author(author: str) -> str:
    author = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", author or "").strip()
    return author or "未知作者"


# --------------------------------------------------------------------- status

class Status:
    """Tiny status writer; the plugin polls this file for progress."""

    def __init__(self, path: Path, job_id: str, url: str):
        self.path = Path(path)
        self.data = {
            "id": job_id,
            "pid": os.getpid(),
            "url": url,
            "status": "running",
            "stage": "download",
            "percent": 0.0,
            "startedAt": now_iso(),
            "updatedAt": now_iso(),
            "error": None,
            "result": None,
        }
        self.flush()

    def update(self, **fields) -> None:
        self.data.update(fields)
        self.data["updatedAt"] = now_iso()
        self.flush()

    def stage(self, stage: str, percent: float | None = None) -> None:
        fields = {"stage": stage}
        if percent is not None:
            fields["percent"] = round(max(0.0, min(100.0, float(percent))), 1)
        self.update(**fields)

    def flush(self) -> None:
        try:
            write_json(self.path, self.data)
        except Exception:
            pass


# --------------------------------------------------------------------- engine

def engine_python(config: dict) -> str:
    return str(config.get("python") or sys.executable)


def engine_dir(config: dict) -> str:
    return str(config.get("engineDir") or "E:/Tools/dsh-shortvideo-export/engine")


def data_home(config: dict) -> str:
    return str(config.get("dataHome") or "E:/Tools/shortvideo-export-home")


def run_cli(config: dict, url: str, engine: str, out_dir: Path, log_path: Path,
            progress_path: Path, status: Status) -> dict:
    """Run `python -m shortvideo ... run <url>` and return its JSON result."""
    python = engine_python(config)
    cmd = [
        python, "-X", "utf8", "-m", "shortvideo",
        "--home", data_home(config),
        "run", url,
        "--formats", "docx",
        "--engine", engine,
        "--out", str(out_dir),
        "--limit", "1",
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = engine_dir(config)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["SV_PROGRESS_FILE"] = str(progress_path)
    env.pop("PYTHONHOME", None)

    log = log_path.open("w", encoding="utf-8", errors="replace")
    log.write("$ " + " ".join(cmd) + "\n")
    log.flush()

    proc = subprocess.Popen(
        cmd, cwd=str(Path(engine_dir(config)).parent), env=env,
        stdout=subprocess.PIPE, stderr=log, text=True, encoding="utf-8",
        errors="replace", bufsize=1,
    )
    status.update(pid=proc.pid)

    out_lines: list[str] = []
    last = 0.0
    while True:
        line = proc.stdout.readline() if proc.stdout else ""
        if line:
            out_lines.append(line)
            log.write(line)
            log.flush()
        if proc.poll() is not None:
            rest = proc.stdout.read() if proc.stdout else ""
            if rest:
                out_lines.append(rest)
                log.write(rest)
            break
        tick = time.time()
        if tick - last > 0.4:
            last = tick
            snap = read_json(progress_path)
            if isinstance(snap, dict):
                stage = str(snap.get("kind") or snap.get("phase") or "")
                percent = snap.get("percent")
                mapped = {"download": "download", "audio": "audio", "extract": "audio",
                          "transcribe": "asr", "asr": "asr"}.get(stage, None)
                if mapped:
                    # 下载 0-45，抽音轨 45-55，识别 55-85
                    if mapped == "download":
                        pct = 5 + 40 * (float(percent or 0) / 100.0)
                    elif mapped == "audio":
                        pct = 45 + 10 * (float(percent or 0) / 100.0)
                    else:
                        pct = 55 + 30 * (float(percent or 0) / 100.0)
                    status.stage(mapped, pct)
    log.close()

    text = "".join(out_lines)
    payload = None
    match = re.search(r"\{\s*\"ok\"", text)
    if match:
        try:
            payload, _end = json.JSONDecoder().raw_decode(text[match.start():])
        except Exception:
            payload = None
    if payload is None:
        raise RuntimeError(f"引擎没有返回结果（退出码 {proc.returncode}），日志：{log_path}")
    if not payload.get("ok"):
        raise RuntimeError(str(payload.get("error") or payload)[:600])
    return payload.get("result") or {}


# --------------------------------------------------------------------- library

def load_library(config: dict):
    import sqlite3
    db = Path(data_home(config)) / "library.db"
    if not db.exists():
        raise RuntimeError(f"找不到素材库：{db}")
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    return con


def fetch_video(con, video_id: str | None) -> dict | None:
    row = None
    if video_id:
        row = con.execute("select * from videos where aweme_id=?", (str(video_id),)).fetchone()
    if row is None:
        row = con.execute("select * from videos order by rowid desc limit 1").fetchone()
    return dict(row) if row else None


def fetch_transcript(con, aweme_id: str) -> dict | None:
    row = con.execute(
        "select * from transcripts where aweme_id=? order by created_at desc, rowid desc limit 1",
        (str(aweme_id),),
    ).fetchone()
    return dict(row) if row else None


def fetch_terms(con) -> dict[str, str]:
    terms: dict[str, str] = {}
    try:
        for row in con.execute("select * from terms"):
            data = dict(row)
            bad = str(data.get("wrong") or data.get("from") or "").strip()
            good = str(data.get("right") or data.get("to") or "").strip()
            if bad and good:
                terms[bad] = good
    except Exception:
        pass
    return terms


# --------------------------------------------------------------------- text

SENT_END = "。！？!?…"
PARA_MIN = 60
PARA_MAX = 115


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?…])", text)
    return [p.strip() for p in parts if p.strip()]


def segment_paragraphs(segments: list[dict], min_chars: int = PARA_MIN,
                       max_chars: int = PARA_MAX) -> list[str]:
    """Pack the transcript into paragraphs of roughly min_chars..max_chars."""
    text = "".join(str(seg.get("text") or "") for seg in segments).strip()
    if not text:
        return []
    sentences = split_sentences(text)
    paras: list[str] = []
    buf = ""
    for sentence in sentences:
        candidate = buf + sentence
        if buf and len(candidate) > max_chars:
            paras.append(buf)
            buf = sentence
        else:
            buf = candidate
        if len(buf) >= min_chars:
            paras.append(buf)
            buf = ""
    if buf.strip():
        if paras and len(buf) < 22:
            paras[-1] = paras[-1] + buf
        else:
            paras.append(buf)
    return [p.strip() for p in paras if p.strip()]


def clean_punctuation(text: str) -> str:
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[，、]{2,}", "，", text)
    text = re.sub(r"[。．]{2,}", "。", text)
    text = re.sub(r"，。", "。", text)
    text = re.sub(r"。，", "。", text)
    text = re.sub(r"^[，。、]+", "", text)
    return text.strip()


def merge_split_paragraphs(paras: list[str]) -> list[str]:
    """Rejoin paragraphs that were cut in the middle of a doubled character.

    The transcriber sometimes splits "...网友纷" | "纷纷跑到..." across two
    windows.  If the tail of one paragraph repeats at the head of the next, the
    duplicate is stitched back instead of showing twice.
    """
    out: list[str] = []
    for para in paras:
        if not out:
            out.append(para)
            continue
        prev = out[-1]
        core = prev.rstrip(SENT_END)
        head = para[:4]
        overlap = 0
        for size in (3, 2, 1):
            if core.endswith(para[:size]) and len(para) > size:
                overlap = size
                break
        if overlap:
            out[-1] = core + para[overlap:]
        elif len(core) >= 6 and head[:1] == core[-1:]:
            out[-1] = core + para[1:]
        else:
            out.append(para)
    return out


def apply_terms(paras: list[str], terms: dict[str, str]) -> tuple[list[str], int]:
    changes = 0
    fixed: list[str] = []
    for para in paras:
        new = para
        for bad, good in terms.items():
            if bad and bad in new:
                new = new.replace(bad, good)
                changes += 1
        fixed.append(new)
    return fixed, changes


def tidy(paras: list[str], terms: dict[str, str]) -> tuple[list[str], int]:
    cleaned = [clean_punctuation(p) for p in paras if p.strip()]
    merged = merge_split_paragraphs(cleaned)
    merged, count = apply_terms(merged, terms)
    merged = [p for p in merged if len(p) >= 6]
    return merged, count


# --------------------------------------------------------------------- proofread

PROOF_SYSTEM = (
    "你是中文短视频文案编辑。用户给一段语音识别出来的文字，分段给出。"
    "你的工作是改错别字和断句：同音字错字、被识别切开的词、句中间不该有的句号、"
    "专有名词（书名、歌名、节目名、人名、地名、平台名）按正确写法修正。"
    "硬性要求：只改错字与断句，不润色、不改写、不删信息、不添加原文没有的内容；"
    "段落数量只能减少或不变，不能增加；总字数与原文字数相差不得超过 30%。"
    "如果某个词听不出来是什么，就保持原样，不要猜。"
    "只输出 JSON：{\"paragraphs\": [\"第一段\", \"第二段\"]}，不要解释。"
)


def call_deepseek(config: dict, paragraphs: list[str], video: dict | None) -> list[str] | None:
    key = str(config.get("deepseekKey") or "").strip()
    if not key:
        return None
    base = str(config.get("deepseekBase") or "https://api.deepseek.com").rstrip("/")
    model = str(config.get("deepseekModel") or "deepseek-chat")
    url = base + "/chat/completions"

    context = ""
    if video:
        context = (
            f"作品标题：{video.get('desc') or ''}\n"
            f"作者：{video.get('author_name') or ''}\n"
            f"时长：{round((video.get('duration_ms') or 0) / 1000)} 秒\n"
        )
    user = context + "\n原文分段：\n" + json.dumps(paragraphs, ensure_ascii=False)
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": PROOF_SYSTEM},
            {"role": "user", "content": user},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")

    request = urllib.request.Request(
        url, data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"[proofread] DeepSeek 调用失败：{exc}\n")
        return None

    try:
        content = payload["choices"][0]["message"]["content"]
        data = json.loads(content)
        paras = [str(p).strip() for p in data.get("paragraphs") or [] if str(p).strip()]
    except Exception as exc:
        sys.stderr.write(f"[proofread] 返回解析失败：{exc}\n")
        return None
    if not paras:
        return None

    # guardrails: never lose the shape of the text, never invent paragraphs
    before = sum(len(p) for p in paragraphs)
    after = sum(len(p) for p in paras)
    if len(paras) > len(paragraphs) + 1:
        sys.stderr.write("[proofread] 段数变多，丢弃\n")
        return None
    if before and not (0.75 <= after / before <= 1.1):
        sys.stderr.write(f"[proofread] 字数比例异常 {after}/{before}，丢弃\n")
        return None
    return paras


def diff_count(before: list[str], after: list[str]) -> int:
    import difflib
    count = 0
    a = "".join(before)
    b = "".join(after)
    matcher = difflib.SequenceMatcher(None, a, b)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            count += max(i2 - i1, j2 - j1)
    return count


# --------------------------------------------------------------------- docx

def write_docx(path: Path, paragraphs: list[str]) -> None:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.shared import Pt

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Microsoft YaHei"
    style.font.size = Pt(12)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
    style.paragraph_format.line_spacing = 1.5
    style.paragraph_format.space_after = Pt(6)

    for text in paragraphs:
        para = doc.add_paragraph(text)
        para.paragraph_format.first_line_indent = Pt(0)

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))


# --------------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--engine", default="")
    parser.add_argument("--job", default="")
    args = parser.parse_args()

    home = Path(args.home)
    home.mkdir(parents=True, exist_ok=True)
    config = read_json(home / "config.json", {}) or {}

    job_id = args.job or ("sf" + datetime.now().strftime("%y%m%d%H%M%S"))
    jobs_dir = home / "jobs"
    job_dir = jobs_dir / job_id
    task_dir = job_dir / "task"
    job_dir.mkdir(parents=True, exist_ok=True)
    task_dir.mkdir(parents=True, exist_ok=True)

    status = Status(job_dir / "status.json", job_id, args.url)
    try:
        engine = args.engine or str(config.get("engine") or "sensevoice")

        status.stage("download", 3)
        result = run_cli(
            config, args.url, engine, task_dir,
            log_path=job_dir / "cli.log",
            progress_path=job_dir / "progress.json",
            status=status,
        )

        video_ids = result.get("videoIds") or []
        video_id = str(video_ids[0]) if video_ids else None
        con = load_library(config)
        try:
            video = fetch_video(con, video_id)
            transcript = fetch_transcript(con, (video or {}).get("aweme_id") or video_id or "")
            terms = fetch_terms(con)
        finally:
            con.close()
        if not video:
            raise RuntimeError("素材库里没有找到这条作品")
        if not transcript:
            raise RuntimeError("没有找到转写结果")

        segments = json.loads(transcript.get("segments_json") or "[]")
        status.stage("asr", 88)

        paragraphs = segment_paragraphs(segments)
        raw = list(paragraphs)
        paragraphs, term_changes = tidy(paragraphs, terms)

        status.stage("proof", 92)
        llm = call_deepseek(config, paragraphs, video)
        if llm:
            proof = {"by": "llm", "model": str(config.get("deepseekModel") or "deepseek-chat"),
                     "changes": diff_count(paragraphs, llm) + term_changes}
            paragraphs = llm
        else:
            proof = {"by": "rules", "model": None, "changes": term_changes}

        title = sanitize_name(str(video.get("desc") or ""))
        author = humanize_author(str(video.get("author_name") or ""))
        out_root = Path(str(config.get("outputDir") or "") or (home / "output")) / author
        out_root.mkdir(parents=True, exist_ok=True)

        docx_path = out_root / f"{title}.docx"
        write_docx(docx_path, paragraphs)

        video_src = Path(str(video.get("file_path") or ""))
        video_path = None
        if video_src.exists():
            video_path = out_root / f"{title}.mp4"
            try:
                if video_path.resolve() != video_src.resolve():
                    shutil.move(str(video_src), str(video_path))
            except Exception:
                video_path = video_src
        elif video_src:
            video_path = video_src

        # drop the intermediate audio track
        audio = video.get("audio_path")
        if audio:
            try:
                Path(str(audio)).unlink(missing_ok=True)
            except Exception:
                pass

        text = "\n".join(paragraphs)
        payload = {
            "ok": True,
            "title": title,
            "author": author,
            "engine": transcript.get("engine") or engine,
            "model": transcript.get("model"),
            "meta": {
                "author": author,
                "duration": round((video.get("duration_ms") or 0) / 1000, 1),
                "width": video.get("width"),
                "height": video.get("height"),
                "size": video.get("file_size") or (video_path.stat().st_size if video_path and video_path.exists() else 0),
            },
            "proof": proof,
            "paragraphs": paragraphs,
            "text": text,
            "wordCount": len(text),
            "rawParagraphs": raw,
            "videoPath": str(video_path) if video_path else None,
            "docxPath": str(docx_path),
        }

        write_json(job_dir / "result.json", payload)
        status.update(status="done", stage="export", percent=100.0, result=payload)
        print()
        print(MARKER)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001 - surfaced to the plugin as text
        message = str(exc)
        status.update(status="error", error=message)
        print()
        print(MARKER)
        print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
