from __future__ import annotations

import json
import os
import re
import subprocess
import shlex
import time
import logging
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import srt

from .config import (
    ASR_LANGUAGE,
    ASR_PROVIDER,
    LOCAL_ASR_COMPUTE_TYPE,
    LOCAL_ASR_DEVICE,
    LOCAL_ASR_MODEL,
    OPENAI_API_KEY,
)
from .openai_client import create_openai_client
from .storage import cookies_file


log = logging.getLogger("uvicorn.error")


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


def _cmd_str(args: list[str]) -> str:
    # Avoid shell=True; log a safe, readable command string.
    return " ".join(shlex.quote(a) for a in args)


def _run_live(args: list[str], cwd: Path | None = None, prefix: str = "proc") -> tuple[int, str]:
    # Stream subprocess output to logs so `docker logs -f` is busy while work is happening.
    cmd_s = _cmd_str(args)
    log.info("%s: start cmd=%s cwd=%s", prefix, cmd_s, str(cwd) if cwd else "")
    t0 = time.time()
    proc = subprocess.Popen(
        args,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    tail: deque[str] = deque(maxlen=250)
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = (raw or "").rstrip("\n")
        if line:
            log.info("%s: %s", prefix, line)
            tail.append(line)
    code = proc.wait()
    dt = time.time() - t0
    log.info("%s: exit=%s seconds=%.1f", prefix, code, dt)
    return int(code), "\n".join(tail)


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
        err = (p.stderr or "").strip()
        if err:
            log.info("subs:list failed exit=%s stderr=%s", p.returncode, err[:2000])
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
    log.info("subs:list langs=%s", ",".join(langs) if langs else "(none)")
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
        "--progress",
        "--newline",
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

    code, tail = _run_live(args, cwd=video_dir, prefix="yt-dlp:subs")
    if code != 0:
        err = tail.strip() or "yt-dlp subtitle download failed"
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
        "--progress",
        "--newline",
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

    code, tail = _run_live(args, cwd=video_dir, prefix="yt-dlp:audio")
    if code != 0:
        raise RuntimeError("yt-dlp audio download failed\n\n" + tail)

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
        "-progress",
        "pipe:1",
        "-nostats",
        "-loglevel",
        "info",
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
    code, tail = _run_live(cmd, cwd=video_dir, prefix="ffmpeg:asr")
    if code != 0:
        raise RuntimeError("ffmpeg transcode failed\n\n" + tail)

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
        "-progress",
        "pipe:1",
        "-nostats",
        "-loglevel",
        "info",
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
    code, tail = _run_live(cmd, cwd=audio_mp3.parent, prefix="ffmpeg:chunk")
    if code != 0:
        raise RuntimeError("ffmpeg chunking failed\n\n" + tail)

    chunks = sorted(chunks_dir.glob("chunk_*.mp3"))
    if not chunks:
        raise RuntimeError("ffmpeg produced no ASR chunks")
    return chunks


def generate_transcript_segments_from_audio(
    url: str,
    video_dir: Path,
    progress: Callable[[int, int], None] | None = None,
    asr_provider: str | None = None,
) -> list[TranscriptSegment]:
    provider = (asr_provider or ASR_PROVIDER or "openai").strip().lower()
    if provider not in ("openai", "local"):
        raise RuntimeError(f"Unknown ASR provider: {provider}")
    if provider == "openai" and not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set (required for OpenAI ASR transcript generation)")

    audio_mp3 = _ensure_asr_audio(url, video_dir)
    chunks = _make_asr_chunks(audio_mp3)

    client = create_openai_client() if provider == "openai" else None

    fw_model = None
    if provider == "local":
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except Exception as e:
            raise RuntimeError(
                "Local ASR is not available. Install backend deps (faster-whisper), or use ASR_PROVIDER=openai.\n\n"
                + str(e)
            )

        # Model downloads on first use; cache at the process level.
        global _FW_MODEL_CACHE  # created below
        key = (LOCAL_ASR_MODEL, LOCAL_ASR_DEVICE, LOCAL_ASR_COMPUTE_TYPE)
        fw_model = _FW_MODEL_CACHE.get(key)
        if fw_model is None:
            log.info(
                "asr: local init model=%s device=%s compute=%s",
                LOCAL_ASR_MODEL,
                LOCAL_ASR_DEVICE,
                LOCAL_ASR_COMPUTE_TYPE,
            )
            fw_model = WhisperModel(LOCAL_ASR_MODEL, device=LOCAL_ASR_DEVICE, compute_type=LOCAL_ASR_COMPUTE_TYPE)
            _FW_MODEL_CACHE[key] = fw_model

    segments: list[TranscriptSegment] = []
    chunk_sec = 300.0
    total = len(chunks)
    if progress is not None:
        try:
            progress(0, total)
        except Exception:
            pass

    for idx, chunk in enumerate(chunks):
        offset = idx * chunk_sec

        # Chunk-level cache so restarts can resume without re-transcribing earlier chunks.
        cache_path = chunk.with_name(chunk.stem + ".asr.json")
        if cache_path.exists() and cache_path.stat().st_size > 0:
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                raw_segments = cached.get("segments")
                if isinstance(raw_segments, list) and raw_segments:
                    for s in raw_segments:
                        try:
                            start = float((s or {}).get("start", 0.0)) + offset
                            end = float((s or {}).get("end", start)) + offset
                            text = str((s or {}).get("text", "")).strip()
                        except Exception:
                            continue
                        if text:
                            segments.append(TranscriptSegment(start_sec=start, end_sec=end, text=text))

                    if progress is not None:
                        try:
                            progress(idx + 1, total)
                        except Exception:
                            pass
                    continue
            except Exception:
                # Ignore corrupt cache; fall through to re-transcribe.
                pass
        if progress is not None:
            try:
                # Heartbeat before the potentially-long OpenAI call.
                progress(idx, total)
            except Exception:
                pass
        log.info("asr: chunk %d/%d file=%s bytes=%d", idx + 1, total, chunk.name, int(chunk.stat().st_size))
        t0 = time.monotonic()
        raw_segments: Any = None

        if provider == "openai":
            assert client is not None
            with chunk.open("rb") as f:
                tr = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                    response_format="verbose_json",
                    language=ASR_LANGUAGE,
                )
            log.info("asr: chunk %d/%d ok provider=openai seconds=%.1f", idx + 1, total, time.monotonic() - t0)

            # Avoid `model_dump()` here: OpenAI's response types are pydantic models and
            # dumping can emit noisy serializer warnings if upstream types drift.
            raw_segments = getattr(tr, "segments", None)
            if raw_segments is None:
                try:
                    data: dict[str, Any] = tr.model_dump() if hasattr(tr, "model_dump") else json.loads(str(tr))
                except Exception:
                    data = {}
                raw_segments = data.get("segments")
        else:
            assert fw_model is not None
            # faster-whisper returns segments relative to this chunk.
            seg_iter, _info = fw_model.transcribe(
                str(chunk),
                language=ASR_LANGUAGE or None,
                vad_filter=True,
            )
            raw_segments = []
            for seg in seg_iter:
                try:
                    raw_segments.append(
                        {
                            "start": float(getattr(seg, "start", 0.0)),
                            "end": float(getattr(seg, "end", 0.0)),
                            "text": str(getattr(seg, "text", "") or "").strip(),
                        }
                    )
                except Exception:
                    continue
            log.info("asr: chunk %d/%d ok provider=local seconds=%.1f", idx + 1, total, time.monotonic() - t0)

        # Best-effort persist chunk result for resume.
        try:
            if isinstance(raw_segments, list) and raw_segments:
                cache_path.write_text(json.dumps({"segments": raw_segments}), encoding="utf-8")
        except Exception:
            pass

        for s in raw_segments or []:
            try:
                if isinstance(s, dict):
                    start_v = s.get("start")
                    end_v = s.get("end")
                    text_v = s.get("text")
                else:
                    start_v = getattr(s, "start", None)
                    end_v = getattr(s, "end", None)
                    text_v = getattr(s, "text", None)

                start = float(start_v or 0.0) + offset
                end = float(end_v or start) + offset
                text = str(text_v or "").strip()
            except Exception:
                continue
            if not text:
                continue
            segments.append(TranscriptSegment(start_sec=start, end_sec=end, text=text))

        if progress is not None:
            try:
                progress(idx + 1, total)
            except Exception:
                pass

    if not segments:
        raise RuntimeError("ASR produced an empty transcript")
    return segments


# Process-level cache for local ASR models.
_FW_MODEL_CACHE: dict[tuple[str, str, str], Any] = {}


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
