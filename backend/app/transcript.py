from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import srt
from openai import OpenAI

from .config import OPENAI_API_KEY
from .storage import cookies_file


_SUB_LANG_RE = re.compile(r"^([A-Za-z0-9][\w-]*)\s+")


UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"


def _proc_debug(args: list[str], p: subprocess.CompletedProcess[str]) -> str:
    stdout = (p.stdout or "").strip()
    stderr = (p.stderr or "").strip()
    if len(stdout) > 6000:
        stdout = stdout[:6000] + "\n…(truncated)"
    if len(stderr) > 6000:
        stderr = stderr[:6000] + "\n…(truncated)"
    return (
        "Command failed\n"
        f"cmd: {' '.join(args)}\n"
        f"exit: {p.returncode}\n"
        f"stdout:\n{stdout}\n\n"
        f"stderr:\n{stderr}\n"
    )


@dataclass(frozen=True)
class TranscriptSegment:
    start_sec: float
    end_sec: float
    text: str


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=str(cwd) if cwd else None, capture_output=True, text=True, check=False)


def _pick_srt_file(video_dir: Path) -> Path | None:
    srts = sorted(video_dir.glob("*.srt"))
    if not srts:
        return None
    # Prefer English if present.
    for p in srts:
        if ".en." in p.name or p.name.endswith(".en.srt"):
            return p
    return srts[0]


def _list_available_sub_langs(url: str, video_dir: Path) -> list[str]:
    # Ask yt-dlp what languages exist first so we only download one.
    # Downloading `all` can trigger rate limits and fails the whole run if any one language 429s.
    args = [
        "yt-dlp",
        "--no-playlist",
        "--extractor-args",
        "youtube:player_client=web",
        "--user-agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    ]
    cf = cookies_file()
    if cf.exists():
        args += ["--cookies", str(cf)]

    args += ["--skip-download", "--list-subs", url]
    p = _run(args, cwd=video_dir)
    if p.returncode != 0:
        return []

    langs: list[str] = []
    for raw in (p.stdout or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        lower = line.lower()
        if lower.startswith("available "):
            continue
        if lower.startswith("language"):
            # header row
            continue
        if line.startswith("["):
            # yt-dlp info lines
            continue

        m = _SUB_LANG_RE.match(line)
        if not m:
            continue
        code = m.group(1)
        if code.lower() == "language":
            continue
        if code not in langs:
            langs.append(code)
    return langs


def _choose_sub_lang(langs: list[str]) -> str | None:
    if not langs:
        return None
    for c in langs:
        if c.lower() == "en":
            return c
    for c in langs:
        if c.lower().startswith("en"):
            return c
    return langs[0]


def download_transcript_srt(url: str, video_dir: Path) -> Path:
    # Hard requirement: captions must exist. We ask for subs and auto-subs; if neither exists, fail.
    # yt-dlp will create files like <title>.<lang>.srt in cwd.
    langs = _list_available_sub_langs(url, video_dir)
    chosen = _choose_sub_lang(langs) or "en"

    args = [
        "yt-dlp",
        "--no-playlist",
        "--extractor-args",
        "youtube:player_client=web",
        "--user-agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    ]
    cf = cookies_file()
    if cf.exists():
        args += ["--cookies", str(cf)]

    args += [
        "--skip-download",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs",
        chosen,
        "--sub-format",
        "srt",
        "-o",
        "subs",
        url,
    ]

    p = _run(args, cwd=video_dir)
    if p.returncode != 0:
        err = (p.stderr or "").strip() or "yt-dlp subtitle download failed"
        if "HTTP Error 429" in err or "Too Many Requests" in err:
            raise RuntimeError(
                "YouTube rate-limited subtitle download (HTTP 429). "
                "If you're using the Chrome side panel, re-run Generate Draft so it uploads captions from the page; "
                "otherwise wait a bit and retry.\n\n" + err
            )
        raise RuntimeError(err)

    picked = _pick_srt_file(video_dir)
    if picked is None:
        raise RuntimeError("No transcript/captions found for this video")
    return picked


def _find_single(prefix: str, video_dir: Path) -> Path | None:
    matches = sorted(video_dir.glob(prefix))
    for p in matches:
        if p.is_file() and not p.name.endswith(".part"):
            return p
    return None


def _download_audio_source(url: str, video_dir: Path) -> Path:
    # Download audio only (source container may be webm/m4a). We'll transcode it for ASR.
    out_tpl = str(video_dir / "asr_source.%(ext)s")
    args = [
        "yt-dlp",
        "--no-playlist",
        "--extractor-args",
        "youtube:player_client=web",
        "--user-agent",
        UA,
        "--impersonate",
        "chrome",
        "-f",
        "bestaudio/best",
        "-o",
        out_tpl,
        url,
    ]
    cf = cookies_file()
    if cf.exists():
        # Put cookies early so yt-dlp applies it to extractor requests.
        args[1:1] = ["--cookies", str(cf)]

    p = _run(args, cwd=video_dir)
    if p.returncode != 0:
        raise RuntimeError(_proc_debug(args, p))

    # yt-dlp wrote asr_source.<ext>
    found = _find_single("asr_source.*", video_dir)
    if found is None:
        raise RuntimeError("Audio download succeeded but output file was not found")
    return found


def _ensure_asr_audio(url: str, video_dir: Path) -> Path:
    # Create a small, whisper-friendly mp3 to keep chunk size manageable.
    out = video_dir / "asr_audio.mp3"
    if out.exists() and out.stat().st_size > 0:
        return out

    src = _download_audio_source(url, video_dir)

    # Transcode to mono 16kHz low bitrate.
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "48k",
        str(out),
    ]
    p = _run(cmd, cwd=video_dir)
    if p.returncode != 0:
        raise RuntimeError(_proc_debug(cmd, p))

    # Best-effort cleanup of the source container.
    try:
        if src.exists():
            src.unlink()
    except Exception:
        pass
    return out


def _make_asr_chunks(audio_mp3: Path, chunk_sec: int = 300) -> list[Path]:
    chunks_dir = audio_mp3.parent / "asr_chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    # If chunks already exist, reuse them.
    existing = sorted(chunks_dir.glob("chunk_*.mp3"))
    if existing:
        return existing

    out_tpl = str(chunks_dir / "chunk_%05d.mp3")
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(audio_mp3),
        "-f",
        "segment",
        "-segment_time",
        str(int(chunk_sec)),
        "-reset_timestamps",
        "1",
        "-c",
        "copy",
        out_tpl,
    ]
    p = _run(cmd, cwd=audio_mp3.parent)
    if p.returncode != 0:
        raise RuntimeError(_proc_debug(cmd, p))

    chunks = sorted(chunks_dir.glob("chunk_*.mp3"))
    if not chunks:
        raise RuntimeError("ffmpeg produced no ASR chunks")
    return chunks


def generate_transcript_segments_from_audio(url: str, video_dir: Path) -> list[TranscriptSegment]:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set (required for ASR transcript generation)")

    audio_mp3 = _ensure_asr_audio(url, video_dir)
    chunks = _make_asr_chunks(audio_mp3)

    client = OpenAI(api_key=OPENAI_API_KEY)

    segments: list[TranscriptSegment] = []
    chunk_sec = 300.0
    for idx, chunk in enumerate(chunks):
        offset = idx * chunk_sec
        with chunk.open("rb") as f:
            tr = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                response_format="verbose_json",
            )
        data: dict[str, Any] = tr.model_dump() if hasattr(tr, "model_dump") else json.loads(str(tr))
        for s in data.get("segments") or []:
            try:
                start = float(s.get("start") or 0.0) + offset
                end = float(s.get("end") or start) + offset
                text = str(s.get("text") or "").strip()
            except Exception:
                continue
            if not text:
                continue
            segments.append(TranscriptSegment(start_sec=start, end_sec=end, text=text))

    if not segments:
        raise RuntimeError("ASR produced an empty transcript")
    return segments


def parse_srt(path: Path) -> list[TranscriptSegment]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    segments: list[TranscriptSegment] = []
    for item in srt.parse(raw):
        start = item.start.total_seconds()
        end = item.end.total_seconds()
        text = " ".join((item.content or "").split())
        if not text:
            continue
        segments.append(TranscriptSegment(start_sec=float(start), end_sec=float(end), text=text))
    if not segments:
        raise RuntimeError("Transcript file was empty")
    return segments


def segments_to_text(segments: list[TranscriptSegment]) -> str:
    return "\n".join(seg.text for seg in segments) + "\n"


def _fmt_ts(sec: float) -> str:
    sec = max(0.0, float(sec))
    m = int(sec // 60)
    s = int(sec % 60)
    h = int(m // 60)
    m = int(m % 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def build_timestamped_minutes(segments: list[TranscriptSegment], max_line_chars: int = 320) -> str:
    # Compress transcript for LLM input: group by minute.
    buckets: dict[int, list[str]] = {}
    for seg in segments:
        minute = int(seg.start_sec // 60)
        buckets.setdefault(minute, []).append(seg.text)

    lines: list[str] = []
    for minute in sorted(buckets.keys()):
        combined = " ".join(buckets[minute])
        combined = " ".join(combined.split())
        if len(combined) > max_line_chars:
            combined = combined[: max_line_chars - 1].rstrip() + "…"
        lines.append(f"{_fmt_ts(minute * 60)} {combined}")

    return "\n".join(lines) + "\n"
