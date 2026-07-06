from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
import subprocess
import logging
import json
import threading
import time
import contextlib
from collections.abc import AsyncIterator

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from .config import ALLOW_ORIGINS, ASR_PROVIDER, OPENAI_TIMEOUT_SEC
from .llm import generate_sections_and_markdown_async
from .pdf import markdown_to_pdf
from .screenshot import auto_ui_change_times, burst_times, capture_screenshot, screenshot_name
from .storage import (
    create_or_get_video_dir,
    cookies_file,
    list_files,
    paths_for_video,
    read_json,
    safe_filename,
    write_json,
)
from .transcript import (
    TranscriptSegment,
    build_timestamped_minutes,
    generate_transcript_segments_from_audio,
    segments_to_text,
)
from .yt import get_video_info


app = FastAPI(title="tldr-youtube", version="0.1.0")

log = logging.getLogger("uvicorn.error")

# In-process tracker so we can tell if a "running" status is actually running in *this* container.
_ACTIVE_GENERATIONS: set[str] = set()

_STATUS_LOCK = threading.Lock()
_STATUS_SUBS: dict[str, set[asyncio.Queue[dict]]] = {}
_MAIN_LOOP: asyncio.AbstractEventLoop | None = None


@app.on_event("startup")
async def _on_startup() -> None:
    global _MAIN_LOOP
    _MAIN_LOOP = asyncio.get_running_loop()

    # "Enable everything" logging: crank up common library loggers.
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for name in (
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "fastapi",
        "httpx",
        "httpcore",
        "openai",
        "asyncio",
    ):
        logging.getLogger(name).setLevel(logging.DEBUG)


def _q_put_drop_old(q: asyncio.Queue[dict], item: dict) -> None:
    try:
        q.put_nowait(item)
        return
    except asyncio.QueueFull:
        try:
            q.get_nowait()
        except Exception:
            pass
        try:
            q.put_nowait(item)
        except Exception:
            pass


def _publish_status(video_id: str, payload: dict) -> None:
    with _STATUS_LOCK:
        subs = list(_STATUS_SUBS.get(video_id) or [])
    if not subs:
        return

    loop = _MAIN_LOOP
    if loop is None or loop.is_closed():
        return

    def _do_put() -> None:
        for q in subs:
            _q_put_drop_old(q, payload)

    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None

    if running is loop:
        _do_put()
    else:
        loop.call_soon_threadsafe(_do_put)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOW_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class FromUrlReq(BaseModel):
    url: str = Field(min_length=5)


class ScreenshotReq(BaseModel):
    t_sec: float
    format: Literal["png", "jpg"] = "png"


class BurstReq(BaseModel):
    center_sec: float
    range_sec: float = 10.0
    interval_sec: float = 1.0
    format: Literal["png", "jpg"] = "png"


class AutoShotsReq(BaseModel):
    start_sec: float = 0.0
    end_sec: float | None = None
    interval_sec: float = 2.0
    threshold: int = 14
    min_gap_sec: float = 15.0
    stability_window: int = 2
    stable_dist: int = 6
    format: Literal["png", "jpg"] = "png"


class SaveMarkdownReq(BaseModel):
    content: str


class ExportPdfReq(BaseModel):
    which: Literal["all", "index", "sections"] = "all"


class TranscriptUploadReq(BaseModel):
    segments: list[dict]  # {startSec,endSec,text}


class CookiesUploadReq(BaseModel):
    cookies: list[dict]


def _video_state(video_id: str) -> dict:
    p = paths_for_video(video_id)
    meta = read_json(p.metadata_json) if p.metadata_json.exists() else {}
    status = read_json(p.status_json) if p.status_json.exists() else {"generation": {"state": "idle"}, "pdf": {"state": "idle"}}
    return {
        "videoId": video_id,
        "dir": str(p.root),
        "metadata": meta,
        "status": status,
        "hasTranscript": p.transcript_txt.exists(),
        "hasSections": p.sections_json.exists(),
        "markdown": list_files(p.markdown_dir, exts={"md"}),
        "screenshots": list_files(p.screenshots_dir, exts={"png", "jpg", "jpeg"}),
        "pdf": list_files(p.pdf_dir, exts={"pdf"}),
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _merge_generation_status(video_id: str, patch: dict) -> dict:
    p = paths_for_video(video_id)
    status = read_json(p.status_json) if p.status_json.exists() else {"generation": {"state": "idle"}, "pdf": {"state": "idle"}}
    cur = status.get("generation") or {}
    cur.update(patch)
    status["generation"] = cur
    write_json(p.status_json, status)
    _publish_status(
        video_id,
        {
            "videoId": video_id,
            "status": status,
            "hasTranscript": p.transcript_txt.exists(),
            "hasSections": p.sections_json.exists(),
            "markdown": list_files(p.markdown_dir, exts={"md"}),
        },
    )
    return status


@app.get("/api/video/{video_id}/status/stream")
async def stream_video_status(video_id: str) -> StreamingResponse:
    # SSE stream of status updates. Emits an initial snapshot immediately.
    p = paths_for_video(video_id)
    q: asyncio.Queue[dict] = asyncio.Queue(maxsize=50)

    with _STATUS_LOCK:
        _STATUS_SUBS.setdefault(video_id, set()).add(q)

    async def gen() -> AsyncIterator[str]:
        try:
            # Initial snapshot.
            status = read_json(p.status_json) if p.status_json.exists() else {"generation": {"state": "idle"}, "pdf": {"state": "idle"}}
            init = {
                "videoId": video_id,
                "status": status,
                "hasTranscript": p.transcript_txt.exists(),
                "hasSections": p.sections_json.exists(),
                "markdown": list_files(p.markdown_dir, exts={"md"}),
            }
            yield f"data: {json.dumps(init)}\n\n"

            # If the client connects after the job already finished, no further events may be published.
            # Close promptly so the frontend doesn't sit on a keepalive-only stream.
            gen_state = ((status or {}).get("generation") or {}).get("state")
            if gen_state in ("done", "error"):
                return

            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {json.dumps(item)}\n\n"
                    st = (item.get("status") or {}).get("generation") or {}
                    if st.get("state") in ("done", "error"):
                        return
                except asyncio.TimeoutError:
                    # Keepalive comment.
                    yield ": ping\n\n"
        finally:
            with _STATUS_LOCK:
                s = _STATUS_SUBS.get(video_id)
                if s is not None:
                    s.discard(q)
                    if not s:
                        _STATUS_SUBS.pop(video_id, None)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            # Reduce the chance of intermediary/proxy buffering killing the stream.
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/debug/ytdlp")
def debug_ytdlp() -> dict:
    def _cmd(args: list[str]) -> dict:
        try:
            p = subprocess.run(args, capture_output=True, text=True, check=False)
            return {
                "cmd": args,
                "code": p.returncode,
                "stdout": (p.stdout or "").strip()[:8000],
                "stderr": (p.stderr or "").strip()[:8000],
            }
        except Exception as e:
            return {"cmd": args, "error": str(e)}

    return {
        "ytDlp": _cmd(["yt-dlp", "--version"]),
        "ytDlpVerbose": _cmd(["yt-dlp", "--verbose"]),
        "deno": _cmd(["deno", "--version"]),
        "node": _cmd(["node", "--version"]),
    }


@app.put("/api/auth/cookies")
def put_auth_cookies(req: CookiesUploadReq) -> dict:
    # Store a Netscape cookie jar so yt-dlp can run as your logged-in session.
    # This is localhost-only, but still sensitive: treat ./data/cookies.txt as a secret.
    allowed_suffixes = ("youtube.com", "google.com")
    dedup: dict[tuple[str, str, str], dict] = {}
    for c in req.cookies or []:
        try:
            domain = str(c.get("domain") or "").strip()
            name = str(c.get("name") or "").strip()
            path = str(c.get("path") or "/").strip() or "/"
            value = str(c.get("value") or "")
        except Exception:
            continue
        if not domain or not name:
            continue
        if not any(domain.lstrip(".").endswith(suf) for suf in allowed_suffixes):
            continue
        dedup[(domain, path, name)] = {
            "domain": domain,
            "path": path,
            "name": name,
            "value": value,
            "secure": bool(c.get("secure")),
            "expirationDate": c.get("expirationDate"),
        }

    items = sorted(dedup.values(), key=lambda x: (x["domain"], x["path"], x["name"]))
    lines = [
        "# Netscape HTTP Cookie File",
        "# Generated by tldr-youtube (local)",
        "",
    ]
    for c in items:
        domain = c["domain"]
        include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
        path = c["path"]
        secure = "TRUE" if c["secure"] else "FALSE"
        exp = c.get("expirationDate")
        try:
            exp_i = int(float(exp)) if exp is not None else 0
        except Exception:
            exp_i = 0

        name = c["name"].replace("\t", " ").replace("\n", " ").replace("\r", " ")
        value = str(c.get("value") or "").replace("\t", " ").replace("\n", " ").replace("\r", " ")
        lines.append("\t".join([domain, include_subdomains, path, secure, str(exp_i), name, value]))

    cf = cookies_file()
    cf.parent.mkdir(parents=True, exist_ok=True)
    cf.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"ok": True, "count": len(items)}


@app.get("/api/auth/status")
def get_auth_status() -> dict:
    cf = cookies_file()
    if not cf.exists():
        return {"hasCookies": False}
    st = cf.stat()
    updated_at = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
    return {
        "hasCookies": True,
        "path": str(cf),
        "updatedAt": updated_at,
        "sizeBytes": int(st.st_size),
    }


@app.delete("/api/auth/cookies")
def delete_auth_cookies() -> dict:
    cf = cookies_file()
    if cf.exists():
        cf.unlink()
    return {"ok": True}


@app.post("/api/video/from-url")
def from_url(req: FromUrlReq) -> dict:
    try:
        info = get_video_info(req.url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    d = create_or_get_video_dir(info.video_id, info.title)
    # Update metadata title/url if changed.
    meta_path = d / "metadata.json"
    meta = read_json(meta_path)
    meta["title"] = info.title
    meta["url"] = info.webpage_url
    write_json(meta_path, meta)
    return _video_state(info.video_id)


@app.get("/api/video/{video_id}")
def get_video(video_id: str) -> dict:
    try:
        return _video_state(video_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Unknown videoId")


async def _generate_draft_job(video_id: str, asr_provider: Literal["openai", "local"] | None = None) -> None:
    p = paths_for_video(video_id)
    log.info("gen[%s] start dir=%s", video_id, p.root)
    _ACTIVE_GENERATIONS.add(video_id)
    now = _now_iso()
    _merge_generation_status(
        video_id,
        {
            "state": "running",
            "step": "init",
            "startedAt": now,
            "updatedAt": now,
        },
    )

    step = "init"
    try:
        meta = read_json(p.metadata_json)
        step = "load_metadata"
        _merge_generation_status(video_id, {"step": step, "updatedAt": _now_iso()})
        log.info("gen[%s] step=%s", video_id, step)
        url = meta.get("url")
        if not url:
            raise RuntimeError("Missing video URL in metadata")
        log.info("gen[%s] url=%s", video_id, url)

        # Transcript (hard requirement)
        step = "transcript"
        _merge_generation_status(video_id, {"step": step, "updatedAt": _now_iso()})
        log.info("gen[%s] step=%s", video_id, step)
        if p.transcript_json.exists():
            tj = read_json(p.transcript_json)
            segs = tj.get("segments") or []
            if not segs:
                raise RuntimeError("Transcript exists but is empty")
            segments = [
                TranscriptSegment(
                    start_sec=float(s["startSec"]),
                    end_sec=float(s["endSec"]),
                    text=str(s["text"]),
                )
                for s in segs
            ]
            log.info("gen[%s] transcript source=%s segments=%d", video_id, tj.get("source"), len(segments))
        else:
            # Ignore YouTube captions/subtitles entirely; always generate transcript via ASR.
            step = "asr_transcript"
            _merge_generation_status(video_id, {"step": step, "updatedAt": _now_iso()})
            log.info("gen[%s] step=%s", video_id, step)

            def _asr_progress(done: int, total: int) -> None:
                _merge_generation_status(
                    video_id,
                    {
                        "step": "asr_transcript",
                        "updatedAt": _now_iso(),
                        "asr": {"done": int(done), "total": int(total)},
                    },
                )
                log.info("gen[%s] asr progress %d/%d", video_id, int(done), int(total))

            resolved_asr = asr_provider or ASR_PROVIDER
            segments = generate_transcript_segments_from_audio(url, p.root, progress=_asr_progress, asr_provider=resolved_asr)
            log.info("gen[%s] asr segments=%d", video_id, len(segments))
            p.transcript_txt.write_text(segments_to_text(segments), encoding="utf-8")
            write_json(
                p.transcript_json,
                {
                    "source": "asr",
                    "asrProvider": resolved_asr,
                    "segments": [{"startSec": s.start_sec, "endSec": s.end_sec, "text": s.text} for s in segments],
                },
            )

        # Make transcript immediately viewable in the extension while the LLM is still running.
        transcript_minutes = build_timestamped_minutes(segments)
        tmd_lines: list[str] = []
        for raw in transcript_minutes.splitlines():
            line = (raw or "").strip()
            if not line:
                continue
            if " " in line:
                ts, rest = line.split(" ", 1)
            else:
                ts, rest = line, ""
            if rest:
                tmd_lines.append(f"- **{ts}** {rest}")
            else:
                tmd_lines.append(f"- **{ts}**")

        transcript_md = (
            f"# Transcript (timestamped minutes)\n\n"
            f"Title: {(meta.get('title') or video_id)}\n\n"
            f"URL: {url}\n\n"
            + "\n".join(tmd_lines)
            + "\n"
        )
        (p.markdown_dir / "00_transcript.md").write_text(transcript_md, encoding="utf-8")
        _merge_generation_status(video_id, {"step": step, "updatedAt": _now_iso(), "transcriptMd": "00_transcript.md"})

        # LLM output
        step = "llm"
        _merge_generation_status(video_id, {"step": step, "updatedAt": _now_iso()})
        log.info("gen[%s] step=%s", video_id, step)
        log.info("gen[%s] transcript_minutes chars=%d", video_id, len(transcript_minutes))

        llm_started = time.monotonic()
        llm_task = asyncio.create_task(
            generate_sections_and_markdown_async(meta.get("title") or "", video_id, url, transcript_minutes)
        )
        while True:
            done, _pending = await asyncio.wait({llm_task}, timeout=5.0)
            if done:
                sections_json, index_md, section_mds = llm_task.result()
                break

            elapsed = time.monotonic() - llm_started
            _merge_generation_status(
                video_id,
                {
                    "step": "llm",
                    "updatedAt": _now_iso(),
                    "llm": {"seconds": int(elapsed)},
                },
            )
            # Hard-stop so a stalled upstream call doesn't leave the job in limbo forever.
            if elapsed > (OPENAI_TIMEOUT_SEC + 5.0):
                llm_task.cancel()
                with contextlib.suppress(Exception):
                    await llm_task
                raise TimeoutError(f"LLM timed out after {int(elapsed)}s")
        log.info(
            "gen[%s] llm ok sections=%d files=%d index_chars=%d",
            video_id,
            len((sections_json or {}).get("sections") or []),
            len(section_mds or []),
            len(index_md or ""),
        )
        write_json(p.sections_json, sections_json)
        (p.markdown_dir / "index.md").write_text(index_md.rstrip() + "\n", encoding="utf-8")
        for s in section_mds:
            (p.markdown_dir / safe_filename(s.file_name)).write_text(s.md.rstrip() + "\n", encoding="utf-8")

        _merge_generation_status(video_id, {"state": "done", "step": "done", "updatedAt": _now_iso()})
        log.info("gen[%s] done", video_id)
    except Exception as e:
        _merge_generation_status(
            video_id,
            {
                "state": "error",
                "step": step,
                "error": str(e),
                "updatedAt": _now_iso(),
            },
        )
        log.exception("gen[%s] error step=%s", video_id, step)
    finally:
        _ACTIVE_GENERATIONS.discard(video_id)


@app.post("/api/video/{video_id}/generate-draft")
def generate_draft(
    video_id: str,
    background: BackgroundTasks,
    force: bool = False,
    asr_provider: Literal["openai", "local"] | None = None,
) -> dict:
    try:
        p = paths_for_video(video_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Unknown videoId")

    status = read_json(p.status_json)
    gen = status.get("generation") or {}
    # Status files can get stuck at {state: running} after a server restart.
    # We only treat it as "actually running" if there's an active job in this process.
    actually_running_here = video_id in _ACTIVE_GENERATIONS
    legacy_stuck_running = gen.get("state") == "running" and not gen.get("startedAt")
    log.info(
        "gen[%s] request force=%s state=%s legacy_stuck_running=%s",
        video_id,
        bool(force),
        gen.get("state"),
        bool(legacy_stuck_running),
    )
    if gen.get("state") == "running" and actually_running_here and not force:
        return {"ok": True, "status": status}

    if gen.get("state") == "running" and not actually_running_here and not force:
        log.info("gen[%s] found orphaned running status; re-queueing", video_id)

    resolved_asr = asr_provider or ASR_PROVIDER
    background.add_task(_generate_draft_job, video_id, resolved_asr)
    status["generation"] = {"state": "queued", "queuedAt": _now_iso(), "asrProvider": resolved_asr}
    write_json(p.status_json, status)
    log.info("gen[%s] queued", video_id)
    return {"ok": True, "status": status}


@app.put("/api/video/{video_id}/transcript")
def put_transcript(video_id: str, req: TranscriptUploadReq) -> dict:
    p = paths_for_video(video_id)
    meta = read_json(p.metadata_json) if p.metadata_json.exists() else {}
    segs = []
    for s in req.segments:
        try:
            start = float(s["startSec"])
            end = float(s["endSec"])
            text = str(s.get("text") or "").strip()
        except Exception:
            continue
        if not text:
            continue
        segs.append({"startSec": start, "endSec": end, "text": text})
    if not segs:
        raise HTTPException(status_code=400, detail="Empty transcript")

    write_json(p.transcript_json, {"source": "extension", "segments": segs})
    p.transcript_txt.write_text("\n".join(s["text"] for s in segs) + "\n", encoding="utf-8")

    # Keep the extension usable even without running the LLM.
    segments = [TranscriptSegment(start_sec=float(s["startSec"]), end_sec=float(s["endSec"]), text=str(s["text"])) for s in segs]
    transcript_minutes = build_timestamped_minutes(segments)
    tmd_lines: list[str] = []
    for raw in transcript_minutes.splitlines():
        line = (raw or "").strip()
        if not line:
            continue
        if " " in line:
            ts, rest = line.split(" ", 1)
        else:
            ts, rest = line, ""
        tmd_lines.append(f"- **{ts}** {rest}".rstrip())
    transcript_md = (
        f"# Transcript (timestamped minutes)\n\n"
        f"Title: {(meta.get('title') or video_id)}\n\n"
        "\n".join(tmd_lines)
        + "\n"
    )
    p.markdown_dir.mkdir(parents=True, exist_ok=True)
    (p.markdown_dir / "00_transcript.md").write_text(transcript_md, encoding="utf-8")

    _merge_generation_status(video_id, {"updatedAt": _now_iso()})
    return {"ok": True, "segments": len(segs)}


@app.get("/api/video/{video_id}/markdown/{name}")
def get_markdown(video_id: str, name: str) -> PlainTextResponse:
    p = paths_for_video(video_id)
    name = safe_filename(name)
    path = p.markdown_dir / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")


@app.put("/api/video/{video_id}/markdown/{name}")
def put_markdown(video_id: str, name: str, req: SaveMarkdownReq) -> dict:
    p = paths_for_video(video_id)
    name = safe_filename(name)
    path = p.markdown_dir / name
    path.write_text(req.content.rstrip() + "\n", encoding="utf-8")
    return {"ok": True}


@app.post("/api/video/{video_id}/screenshot")
def post_screenshot(video_id: str, req: ScreenshotReq) -> dict:
    p = paths_for_video(video_id)
    meta = read_json(p.metadata_json)
    url = meta.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="Missing video URL")

    fmt = req.format
    name = screenshot_name(req.t_sec, "manual", None, fmt)
    out = p.screenshots_dir / name
    try:
        capture_screenshot(url, req.t_sec, out, fmt=fmt)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "file": name}


@app.post("/api/video/{video_id}/screenshot/burst")
def post_screenshot_burst(video_id: str, req: BurstReq) -> dict:
    p = paths_for_video(video_id)
    meta = read_json(p.metadata_json)
    url = meta.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="Missing video URL")

    fmt = req.format
    times = burst_times(req.center_sec, req.range_sec, req.interval_sec)
    files: list[str] = []
    for idx, t in enumerate(times):
        name = screenshot_name(t, "burst", idx, fmt)
        out = p.screenshots_dir / name
        capture_screenshot(url, t, out, fmt=fmt)
        files.append(name)
    return {"ok": True, "files": files}


@app.post("/api/video/{video_id}/screenshot/auto")
def post_screenshot_auto(video_id: str, req: AutoShotsReq) -> dict:
    p = paths_for_video(video_id)
    meta = read_json(p.metadata_json)
    url = meta.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="Missing video URL")

    fmt = req.format
    try:
        times = auto_ui_change_times(
            url,
            p.root,
            start_sec=req.start_sec,
            end_sec=req.end_sec,
            interval_sec=req.interval_sec,
            threshold=req.threshold,
            min_gap_sec=req.min_gap_sec,
            stability_window=req.stability_window,
            stable_dist=req.stable_dist,
            include_start=True,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    files: list[str] = []
    for idx, t in enumerate(times):
        name = screenshot_name(t, "auto", idx, fmt)
        out = p.screenshots_dir / name
        try:
            capture_screenshot(url, t, out, fmt=fmt)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"capture failed at t={t:.3f}: {e}")
        files.append(name)
    return {"ok": True, "files": files, "times": times}


@app.get("/api/video/{video_id}/screenshot/{name}")
def get_screenshot(video_id: str, name: str) -> FileResponse:
    p = paths_for_video(video_id)
    name = safe_filename(name)
    path = p.screenshots_dir / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(str(path))


@app.delete("/api/video/{video_id}/screenshot/{name}")
def delete_screenshot(video_id: str, name: str) -> dict:
    p = paths_for_video(video_id)
    name = safe_filename(name)
    path = p.screenshots_dir / name
    if path.exists():
        path.unlink()
    return {"ok": True}


async def _export_pdf_job(video_id: str, which: str) -> None:
    p = paths_for_video(video_id)
    status = read_json(p.status_json)
    status["pdf"] = {"state": "running", "which": which}
    write_json(p.status_json, status)
    try:
        meta = read_json(p.metadata_json)
        title = meta.get("title") or video_id

        if which in ("all", "index"):
            index_path = p.markdown_dir / "index.md"
            if index_path.exists():
                out = p.pdf_dir / "00_index.pdf"
                await asyncio.to_thread(markdown_to_pdf, index_path.read_text(encoding="utf-8"), p.root, out, title)

        if which in ("all", "sections"):
            for md_name in list_files(p.markdown_dir, exts={"md"}):
                if md_name == "index.md":
                    continue
                md_path = p.markdown_dir / md_name
                out = p.pdf_dir / (md_path.stem + ".pdf")
                await asyncio.to_thread(
                    markdown_to_pdf,
                    md_path.read_text(encoding="utf-8"),
                    p.root,
                    out,
                    f"{title} - {md_path.stem}",
                )

        status = read_json(p.status_json)
        status["pdf"] = {"state": "done", "which": which}
        write_json(p.status_json, status)
    except Exception as e:
        status = read_json(p.status_json)
        status["pdf"] = {"state": "error", "error": str(e)}
        write_json(p.status_json, status)


@app.post("/api/video/{video_id}/export-pdf")
def export_pdf(video_id: str, req: ExportPdfReq, background: BackgroundTasks) -> dict:
    try:
        p = paths_for_video(video_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Unknown videoId")

    status = read_json(p.status_json)
    if status.get("pdf", {}).get("state") == "running":
        return {"ok": True, "status": status}

    background.add_task(_export_pdf_job, video_id, req.which)
    status["pdf"] = {"state": "queued", "which": req.which}
    write_json(p.status_json, status)
    return {"ok": True, "status": status}


@app.get("/api/video/{video_id}/pdf/{name}")
def get_pdf(video_id: str, name: str) -> FileResponse:
    p = paths_for_video(video_id)
    name = safe_filename(name)
    path = p.pdf_dir / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(str(path), media_type="application/pdf")
