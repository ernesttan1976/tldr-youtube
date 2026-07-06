from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Iterable

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
    # Prefer a locally cached, video-capable source file to avoid brittle direct
    # access to `googlevideo.com` URLs from ffmpeg.
    # Layout: <video_dir>/screenshots/<file>
    video_dir = out_path.parent.parent
    local_src = _find_single(video_dir, "shot_source.*")
    if local_src is not None:
        stream = str(local_src)
    else:
        # Prefer using a direct stream URL so we don't block on downloading a full local file.
        # If that fails (403/expiry/etc), fall back to downloading a small local source.
        try:
            stream = get_stream_url(url)
        except Exception:
            stream = str(_ensure_shot_source(url, video_dir))

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


def _ffprobe_duration_sec(src: str) -> float | None:
    p = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nk=1:nw=1",
            src,
        ]
    )
    if p.returncode != 0:
        return None
    try:
        v = float((p.stdout or "").strip())
        if not math.isfinite(v) or v <= 0:
            return None
        return v
    except Exception:
        return None


def _hamming64(a: int, b: int) -> int:
    return int((a ^ b).bit_count())


def _dhash_9x8_gray(frame72: bytes) -> int:
    # 9x8 grayscale bytes (row-major). Produces a 64-bit dHash.
    if len(frame72) != 72:
        raise ValueError("expected 72 bytes for 9x8 frame")
    bits = 0
    k = 0
    for y in range(8):
        row = frame72[y * 9 : y * 9 + 9]
        for x in range(8):
            if row[x] < row[x + 1]:
                bits |= 1 << k
            k += 1
    return bits


def _read_raw_frames_9x8_gray(stream: str, start_sec: float, dur_sec: float, interval_sec: float) -> list[int]:
    # Emits one 9x8 grayscale frame every interval_sec, then converts each to a dHash.
    start = max(0.0, float(start_sec))
    dur = max(0.0, float(dur_sec))
    interval = max(0.2, float(interval_sec))

    http_args: list[str] = []
    if "://" in stream:
        http_args = ["-user_agent", _YT_UA, "-headers", _YT_HEADERS]

    # NOTE: scale to 9x8 so we can compute dHash directly from the raw bytes.
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        *http_args,
        "-ss",
        f"{start:.3f}",
        "-i",
        stream,
        "-t",
        f"{dur:.3f}",
        "-vf",
        f"fps=1/{interval:.6f},scale=9:8:flags=fast_bilinear,format=gray",
        "-pix_fmt",
        "gray",
        "-f",
        "rawvideo",
        "pipe:1",
    ]

    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = p.communicate()
    if p.returncode != 0:
        msg = (err or b"").decode("utf-8", errors="ignore").strip() or "ffmpeg frame sampling failed"
        raise RuntimeError(msg)

    frame_size = 72
    n = len(out) // frame_size
    hashes: list[int] = []
    for i in range(n):
        frame = out[i * frame_size : (i + 1) * frame_size]
        try:
            hashes.append(_dhash_9x8_gray(frame))
        except Exception:
            continue
    return hashes


def auto_ui_change_times(
    url: str,
    video_dir: Path,
    *,
    start_sec: float = 0.0,
    end_sec: float | None = None,
    interval_sec: float = 2.0,
    threshold: int = 14,
    min_gap_sec: float = 15.0,
    stability_window: int = 2,
    stable_dist: int = 6,
    include_start: bool = True,
) -> list[float]:
    # UI-change detector based on dHash distance between sampled frames.
    # It aims to catch persistent interface/layout changes and ignore transient motion.
    # Prefer direct stream URL first so we don't block on downloading the full video.
    # If ffmpeg can't read the stream URL reliably, fall back to a local shot_source.*.
    src = _find_single(video_dir, "shot_source.*")
    stream = str(src) if src is not None else get_stream_url(url)

    dur_total = _ffprobe_duration_sec(stream)
    start = max(0.0, float(start_sec))
    if end_sec is None:
        end = dur_total if dur_total is not None else None
    else:
        end = max(start, float(end_sec))
        if dur_total is not None:
            end = min(end, dur_total)

    if end is None:
        # Unknown duration: sample a fixed budget so we don't run forever.
        end = start + 15 * 60.0

    dur = max(0.0, float(end - start))
    if dur <= 0.2:
        return [start] if include_start else []

    try:
        hashes = _read_raw_frames_9x8_gray(stream, start, dur, interval_sec)
    except Exception:
        # Last resort: download a local video source and retry.
        stream = str(_ensure_shot_source(url, video_dir))
        hashes = _read_raw_frames_9x8_gray(stream, start, dur, interval_sec)
    if not hashes:
        return [start] if include_start else []

    interval = max(0.2, float(interval_sec))
    times = [start + i * interval for i in range(len(hashes))]

    picked: list[float] = []
    last_pick_t = -1e9
    last_pick_h: int | None = None
    if include_start:
        picked.append(times[0])
        last_pick_t = times[0]
        last_pick_h = hashes[0]

    stab = max(0, int(stability_window))
    thresh = max(1, int(threshold))
    min_gap = max(0.0, float(min_gap_sec))
    stable_d = max(0, int(stable_dist))

    # Greedy scan: detect a big jump from i-1 -> i, then require that i persists for a bit.
    # This rejects short animations/cursor movement.
    for i in range(1, len(hashes) - stab):
        t = times[i]
        if t - last_pick_t < min_gap:
            continue

        if _hamming64(hashes[i - 1], hashes[i]) < thresh:
            continue

        ok = True
        for j in range(1, stab + 1):
            if _hamming64(hashes[i], hashes[i + j]) > stable_d:
                ok = False
                break
        if not ok:
            continue

        if last_pick_h is not None and _hamming64(last_pick_h, hashes[i]) < thresh:
            continue

        picked.append(t)
        last_pick_t = t
        last_pick_h = hashes[i]

    # Clamp to end.
    return [t for t in picked if t <= end + 0.001]
