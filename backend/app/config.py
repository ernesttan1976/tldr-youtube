import os


def _env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return v


DATA_DIR = _env("DATA_DIR", "/data")
OPENAI_API_KEY = _env("OPENAI_API_KEY")
OPENAI_MODEL = _env("OPENAI_MODEL", "gpt-4.1-mini")

# ASR (speech-to-text) provider for transcript generation.
# "openai" uses whisper-1 via OpenAI; "local" uses faster-whisper in this container.
ASR_PROVIDER = (_env("ASR_PROVIDER", "openai") or "openai").strip().lower()
ASR_LANGUAGE = (_env("ASR_LANGUAGE", "en") or "en").strip().lower()

# Local ASR settings (only used when ASR_PROVIDER=local or overridden per request).
LOCAL_ASR_MODEL = _env("LOCAL_ASR_MODEL", "small.en") or "small.en"
LOCAL_ASR_DEVICE = (_env("LOCAL_ASR_DEVICE", "cpu") or "cpu").strip().lower()
LOCAL_ASR_COMPUTE_TYPE = (_env("LOCAL_ASR_COMPUTE_TYPE", "int8") or "int8").strip().lower()

# OpenAI SDK (httpx) settings.
# Keep these finite so background jobs can't hang forever on a stalled network request.
OPENAI_TIMEOUT_SEC = float(_env("OPENAI_TIMEOUT_SEC", "600") or "600")
OPENAI_MAX_RETRIES = int(_env("OPENAI_MAX_RETRIES", "2") or "2")

HOST = _env("HOST", "127.0.0.1")
PORT = int(_env("PORT", "4711") or "4711")

ALLOW_ORIGINS = [o.strip() for o in (_env("ALLOW_ORIGINS", "*") or "*").split(",")]
