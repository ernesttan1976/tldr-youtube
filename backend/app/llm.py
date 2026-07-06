from __future__ import annotations

import json
from dataclasses import dataclass

from .config import OPENAI_MODEL
from .openai_client import create_openai_client


@dataclass(frozen=True)
class SectionMd:
    file_name: str
    title: str
    start_sec: float
    end_sec: float
    md: str


def generate_sections_and_markdown(title: str, video_id: str, url: str, transcript_minutes: str) -> tuple[dict, str, list[SectionMd]]:
    client = create_openai_client()

    system = (
        "You create study guides for YouTube tutorials. "
        "You must return STRICT JSON with no extra keys, no markdown fences."
    )

    user = {
        "title": title,
        "videoId": video_id,
        "url": url,
        "transcriptMinutes": transcript_minutes,
        "requirements": {
            "markdownFirst": True,
            "oneFilePerSection": True,
            "includeTimestamps": True,
        },
        "output": {
            "sectionsJson": True,
            "indexMarkdown": True,
            "sectionMarkdown": True,
        },
    }

    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string"},
                        "startSec": {"type": "number"},
                        "endSec": {"type": "number"},
                        "fileName": {"type": "string"},
                        "md": {"type": "string"},
                    },
                    "required": ["title", "startSec", "endSec", "fileName", "md"],
                },
            },
            "indexMd": {"type": "string"},
        },
        "required": ["sections", "indexMd"],
    }

    prompt = (
        "Create a structured study guide. Use the transcriptMinutes lines (each line starts with a timestamp) to infer "
        "section boundaries. Keep sections non-overlapping and cover the full video. "
        "Return JSON that matches the provided JSON Schema. "
        "fileName must be like '01_intro.md' (2-digit prefix)."
    )

    raw: str
    try:
        # Newer OpenAI SDKs expose the Responses API.
        resp = client.responses.create(
            model=OPENAI_MODEL,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt + "\n\n" + json.dumps(user)},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "StudyGuide",
                    "schema": schema,
                    "strict": True,
                }
            },
        )
        raw = resp.output_text
    except AttributeError:
        # Some installations expose OpenAI() but don't have `client.responses`.
        # Fall back to chat.completions with JSON mode.
        cc = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt + "\n\n" + json.dumps(user)},
            ],
            response_format={"type": "json_object"},
        )
        raw = (cc.choices[0].message.content or "").strip()

    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("LLM returned non-object JSON")
    if "sections" not in data or "indexMd" not in data:
        raise RuntimeError("LLM JSON missing required keys: sections, indexMd")
    if not isinstance(data.get("sections"), list) or not isinstance(data.get("indexMd"), str):
        raise RuntimeError("LLM JSON has invalid types for sections/indexMd")
    sections = data["sections"]
    index_md = data["indexMd"]

    sections_json = {
        "title": title,
        "videoId": video_id,
        "url": url,
        "sections": [
            {
                "title": s["title"],
                "startSec": float(s["startSec"]),
                "endSec": float(s["endSec"]),
                "fileName": s["fileName"],
            }
            for s in sections
        ],
    }

    section_mds: list[SectionMd] = []
    for s in sections:
        section_mds.append(
            SectionMd(
                file_name=s["fileName"],
                title=s["title"],
                start_sec=float(s["startSec"]),
                end_sec=float(s["endSec"]),
                md=s["md"],
            )
        )

    return sections_json, index_md, section_mds
