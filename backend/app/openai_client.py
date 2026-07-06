from __future__ import annotations

import httpx
from openai import DefaultHttpxClient, OpenAI

from .config import OPENAI_API_KEY, OPENAI_MAX_RETRIES, OPENAI_TIMEOUT_SEC


def create_openai_client() -> OpenAI:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")

    # Be explicit about httpx timeouts so stalled uploads/downloads can't hang forever.
    timeout = httpx.Timeout(
        timeout=OPENAI_TIMEOUT_SEC,
        connect=min(10.0, OPENAI_TIMEOUT_SEC),
        read=OPENAI_TIMEOUT_SEC,
        write=min(60.0, OPENAI_TIMEOUT_SEC),
        pool=min(10.0, OPENAI_TIMEOUT_SEC),
    )

    return OpenAI(
        api_key=OPENAI_API_KEY,
        max_retries=OPENAI_MAX_RETRIES,
        http_client=DefaultHttpxClient(timeout=timeout),
    )
