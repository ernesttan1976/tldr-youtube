import os


def _env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return v


DATA_DIR = _env("DATA_DIR", "/data")
OPENAI_API_KEY = _env("OPENAI_API_KEY")
OPENAI_MODEL = _env("OPENAI_MODEL", "gpt-4.1-mini")

HOST = _env("HOST", "127.0.0.1")
PORT = int(_env("PORT", "4711") or "4711")

ALLOW_ORIGINS = [o.strip() for o in (_env("ALLOW_ORIGINS", "*") or "*").split(",")]
