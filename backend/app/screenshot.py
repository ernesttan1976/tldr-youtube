from __future__ import annotations

import math
import subprocess
from pathlib import Path

from .yt import get_stream_url


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def _ts_label(t_sec: float) -> str:
    t = max(0, int(round(float(t_sec))))
    return f"t{t:06d}"


def capture_screenshot(url: str, t_sec: float, out_path: Path, fmt: str = "png") -> None:
    stream = get_stream_url(url)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out = str(out_path)

    t_sec = max(0.0, float(t_sec))
    # -ss before -i is fast; adequate for tutorial screenshots.
    if fmt.lower() == "jpg" or fmt.lower() == "jpeg":
        args = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
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
