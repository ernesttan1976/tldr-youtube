from __future__ import annotations

import json
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .storage import cookies_file


@dataclass(frozen=True)
class VideoInfo:
    video_id: str
    title: str
    webpage_url: str


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def _base_args() -> list[str]:
    # youtube extractor tweaks reduce breakage when YouTube changes clients.
    args = [
        "--no-playlist",
        "--extractor-args",
        "youtube:player_client=web",
        "--user-agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    ]

    cf = cookies_file()
    if cf.exists():
        args += ["--cookies", str(cf)]
    return args


def parse_video_id(url: str) -> str | None:
    try:
        u = urllib.parse.urlparse(url)
    except Exception:
        return None

    host = (u.netloc or "").lower()
    path = u.path or ""
    qs = urllib.parse.parse_qs(u.query or "")

    if "v" in qs and qs["v"]:
        return qs["v"][0]
    if host.endswith("youtu.be"):
        vid = path.lstrip("/").split("/")[0]
        return vid or None
    if path.startswith("/shorts/"):
        vid = path.split("/shorts/")[-1].split("/")[0]
        return vid or None
    return None


def _oembed_title(url: str) -> str | None:
    try:
        q = urllib.parse.urlencode({"url": url, "format": "json"})
        oembed = f"https://www.youtube.com/oembed?{q}"
        with urllib.request.urlopen(oembed, timeout=12) as r:
            data = json.loads(r.read().decode("utf-8"))
        t = str(data.get("title") or "").strip()
        return t or None
    except Exception:
        return None


def get_video_info(url: str) -> VideoInfo:
    # yt-dlp gives consistent metadata and video id.
    p = _run(["yt-dlp", *_base_args(), "--dump-single-json", url])
    if p.returncode == 0:
        data: dict[str, Any] = json.loads(p.stdout)
        video_id = str(data.get("id") or "").strip()
        title = str(data.get("title") or "").strip()
        webpage_url = str(data.get("webpage_url") or url).strip()
        if video_id and title:
            return VideoInfo(video_id=video_id, title=title, webpage_url=webpage_url)

    # Fallback: best-effort ID parse + oEmbed title.
    vid = parse_video_id(url)
    if not vid:
        raise RuntimeError(p.stderr.strip() or "yt-dlp failed")

    title = _oembed_title(url) or f"video-{vid}"
    return VideoInfo(video_id=vid, title=title, webpage_url=url)


def get_stream_url(url: str) -> str:
    # Prefer a direct MP4 URL for ffmpeg. yt-dlp prints one URL per line for -g.
    fmt = "best"
    p = _run(["yt-dlp", *_base_args(), "-f", fmt, "-g", url])
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or "yt-dlp failed to get stream URL")
    lines = [ln.strip() for ln in p.stdout.splitlines() if ln.strip()]
    if not lines:
        raise RuntimeError("yt-dlp returned no stream URL")
    return lines[0]
