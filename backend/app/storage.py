from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_DIR


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(s: str) -> str:
    # Minimal ASCII slugify; avoid new deps.
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s or "video"


@dataclass(frozen=True)
class VideoPaths:
    root: Path
    metadata_json: Path
    transcript_txt: Path
    transcript_json: Path
    sections_json: Path
    markdown_dir: Path
    screenshots_dir: Path
    pdf_dir: Path
    status_json: Path


def data_root() -> Path:
    return Path(DATA_DIR).resolve()


def videos_root() -> Path:
    return data_root() / "videos"


def cookies_file() -> Path:
    return data_root() / "cookies.txt"


def ensure_dirs() -> None:
    (videos_root()).mkdir(parents=True, exist_ok=True)


def find_video_dir_by_id(video_id: str) -> Path | None:
    root = videos_root()
    if not root.exists():
        return None
    for child in root.iterdir():
        if not child.is_dir():
            continue
        # folder name ends with __<videoId>
        if child.name.endswith(f"__{video_id}"):
            return child
    return None


def create_or_get_video_dir(video_id: str, title: str) -> Path:
    ensure_dirs()

    existing = find_video_dir_by_id(video_id)
    if existing is not None:
        return existing

    slug = _slugify(title)
    base = videos_root() / f"{slug}__{video_id}"

    # Ensure uniqueness even if title collides for same videoId (unlikely) or stale dir.
    path = base
    i = 2
    while path.exists():
        path = videos_root() / f"{slug}-{i}__{video_id}"
        i += 1

    path.mkdir(parents=True, exist_ok=True)
    (path / "markdown").mkdir(exist_ok=True)
    (path / "screenshots").mkdir(exist_ok=True)
    (path / "pdf").mkdir(exist_ok=True)

    write_json(
        path / "metadata.json",
        {
            "videoId": video_id,
            "title": title,
            "createdAt": _now_iso(),
        },
    )
    write_json(path / "status.json", {"generation": {"state": "idle"}, "pdf": {"state": "idle"}})
    return path


def paths_for_video(video_id: str) -> VideoPaths:
    d = find_video_dir_by_id(video_id)
    if d is None:
        raise FileNotFoundError(f"Unknown videoId: {video_id}")

    return VideoPaths(
        root=d,
        metadata_json=d / "metadata.json",
        transcript_txt=d / "transcript.txt",
        transcript_json=d / "transcript.json",
        sections_json=d / "sections.json",
        markdown_dir=d / "markdown",
        screenshots_dir=d / "screenshots",
        pdf_dir=d / "pdf",
        status_json=d / "status.json",
    )


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def list_files(dir_path: Path, exts: set[str] | None = None) -> list[str]:
    if not dir_path.exists():
        return []
    out: list[str] = []
    for p in sorted(dir_path.iterdir()):
        if not p.is_file():
            continue
        if exts is not None and p.suffix.lower().lstrip(".") not in exts:
            continue
        out.append(p.name)
    return out


def safe_filename(name: str) -> str:
    # Keep to a safe subset; reject path traversal.
    if name != os.path.basename(name):
        raise ValueError("Invalid filename")
    if ".." in name or name.startswith("/") or name.startswith("\\"):
        raise ValueError("Invalid filename")
    return name
