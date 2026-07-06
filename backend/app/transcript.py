from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import srt

from .storage import cookies_file


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


def download_transcript_srt(url: str, video_dir: Path) -> Path:
    # Hard requirement: captions must exist. We ask for subs and auto-subs; if neither exists, fail.
    # yt-dlp will create files like <title>.<lang>.srt in cwd.
    args = [
        "yt-dlp",
        "--no-playlist",
        "--extractor-args",
        "youtube:player_client=android,web",
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
        "all",
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
