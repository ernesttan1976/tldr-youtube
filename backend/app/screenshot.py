from __future__ import annotations

import math
import subprocess
from pathlib import Path

from .storage import cookies_file
from .yt import get_stream_url


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)


# ffmpeg hits `googlevideo.com` URLs directly. Those endpoints often 403 unless we send
# browser-like headers (at minimum a modern UA + referer).
_YT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
_YT_HEADERS = "\r\n".join(
    [
        "Referer: https://www.youtube.com/",
        "Origin: https://www.youtube.com",
        "Accept: */*",
    ]
) + "\r\n"


def _ts_label(t_sec: float) -> str:
    t = max(0, int(round(float(t_sec))))
    return f"t{t:06d}"


def _find_single(video_dir: Path, pattern: str) -> Path | None:
    try:
        for p in sorted(video_dir.glob(pattern)):
            if p.is_file() and not p.name.endswith(".part") and p.stat().st_size > 0:
                return p
    except Exception:
        return None
    return None


def _ensure_shot_source(url: str, video_dir: Path) -> Path:
    # Keep screenshot capture reliable by having a local, video-capable source.
    existing = _find_single(video_dir, "shot_source.*")
    if existing is not None:
        return existing

    out_tpl = str(video_dir / "shot_source.%(ext)s")
    args = [
        "yt-dlp",
        "--no-playlist",
        "--extractor-args",
        "youtube:player_client=web",
        "--user-agent",
        _YT_UA,
        "--impersonate",
        "chrome",
        "-f",
        # Best pre-merged MP4 when possible (good for ffmpeg screenshots).
        "b[ext=mp4]/b",
        "-o",
        out_tpl,
        url,
    ]
    cf = cookies_file()
    if cf.exists():
        args[1:1] = ["--cookies", str(cf)]

    p = _run(args)
    if p.returncode != 0:
        msg = (p.stderr or "").strip() or (p.stdout or "").strip() or "yt-dlp failed to download screenshot source"
        raise RuntimeError(msg)

    found = _find_single(video_dir, "shot_source.*")
    if found is None:
        raise RuntimeError("yt-dlp succeeded but shot_source output file was not found")
    return found


def capture_screenshot(url: str, t_sec: float, out_path: Path, fmt: str = "png") -> None:
    # Prefer a locally cached source file (created during ASR) to avoid brittle direct
    # access to `googlevideo.com` URLs from ffmpeg.
    # Layout: <video_dir>/screenshots/<file>
    video_dir = out_path.parent.parent
    local_src = _find_single(video_dir, "asr_source.*")
    if local_src is not None:
        stream = str(local_src)
    else:
        # If the video hasn't been processed yet, download a small local source for screenshots.
        # Falling back to direct stream URLs is brittle (ffmpeg often gets 403 from googlevideo).
        try:
            stream = str(_ensure_shot_source(url, video_dir))
        except Exception:
            stream = get_stream_url(url)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out = str(out_path)

    t_sec = max(0.0, float(t_sec))
    # -ss before -i is fast; adequate for tutorial screenshots.
    # Only send extra HTTP headers when the input is a URL.
    http_args: list[str] = []
    if "://" in stream:
        http_args = ["-user_agent", _YT_UA, "-headers", _YT_HEADERS]

    if fmt.lower() == "jpg" or fmt.lower() == "jpeg":
        args = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            *http_args,
            "-ss",
            f"{t_sec:.3f}",
            "-i",
            stream,
            "-frames:v",
            "1",
            "-q:v",
            "2",
            out,
        ]
    else:
        args = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            *http_args,
            "-ss",
            f"{t_sec:.3f}",
            "-i",
            stream,
            "-frames:v",
            "1",
            "-c:v",
            "png",
            out,
        ]

    p = _run(args)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or "ffmpeg screenshot failed")


def burst_times(center_sec: float, range_sec: float, interval_sec: float) -> list[float]:
    center = float(center_sec)
    r = float(range_sec)
    interval = max(0.1, float(interval_sec))
    start = max(0.0, center - r)
    end = max(0.0, center + r)

    n = int(math.floor((end - start) / interval))
    times = [start + i * interval for i in range(n + 1)]
    # Ensure center included.
    if all(abs(t - center) > (interval / 2) for t in times):
        times.append(center)
        times.sort()
    return times


def screenshot_name(t_sec: float, kind: str, idx: int | None, fmt: str) -> str:
    base = _ts_label(t_sec)
    if idx is None:
        return f"{base}_{kind}.{fmt}"
    return f"{base}_{kind}_{idx:02d}.{fmt}"
