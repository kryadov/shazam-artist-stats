"""Configuration: `.env` parsing and selection of the statistics sources.

Precedence for every setting: CLI flag -> real environment -> `.env` next to
the script -> default.
"""

import os
from dataclasses import dataclass
from typing import Optional

ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
VALID_SOURCES = ("shazam", "youtube")


class ConfigError(Exception):
    pass


@dataclass
class Config:
    sources: list  # ordered; the first one is the default sort key
    youtube_api_key: str = ""


# --------------------------------------------------------------------------- #
# .env
# --------------------------------------------------------------------------- #
def parse_env(text: str) -> dict:
    """Parse KEY=VALUE lines. Supports comments, `export`, and quoted values."""
    out = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key:
            continue
        value = value.strip()
        if value[:1] in ("'", '"') and value.find(value[0], 1) > 0:
            # Quoted: take everything up to the closing quote, '#' included.
            value = value[1:value.find(value[0], 1)]
        else:
            # Unquoted: ' #' starts an inline comment (as in `SOURCES=shazam  # note`).
            hash_pos = value.find(" #")
            if hash_pos >= 0:
                value = value[:hash_pos].rstrip()
        out[key] = value
    return out


def load_env(path: str = ENV_FILE) -> dict:
    try:
        # utf-8-sig: Notepad on Windows may save the file with a BOM.
        with open(path, encoding="utf-8-sig") as f:
            return parse_env(f.read())
    except FileNotFoundError:
        return {}


def setting(name: str, dotenv: dict, environ: Optional[dict] = None,
            default: str = "") -> str:
    """Real environment wins over `.env`; empty values count as unset."""
    environ = os.environ if environ is None else environ
    return environ.get(name) or dotenv.get(name) or default


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #
def parse_sources(value: str) -> list:
    sources = []
    for part in value.split(","):
        name = part.strip().lower()
        if not name:
            continue
        if name not in VALID_SOURCES:
            raise ConfigError(f"Unknown source \"{name}\" "
                              f"(valid: {', '.join(VALID_SOURCES)}).")
        if name not in sources:
            sources.append(name)
    if not sources:
        raise ConfigError(f"No sources selected (valid: {', '.join(VALID_SOURCES)}).")
    return sources


def load_config(sources_flag: Optional[str] = None, no_youtube: bool = False,
                dotenv: Optional[dict] = None,
                environ: Optional[dict] = None) -> Config:
    """Resolve which sources to collect. Fails before any network request."""
    dotenv = load_env() if dotenv is None else dotenv
    key = setting("YOUTUBE_API_KEY", dotenv, environ)
    if no_youtube:
        return Config(sources=["shazam"], youtube_api_key=key)

    raw = sources_flag or setting("SOURCES", dotenv, environ)
    if raw:
        sources = parse_sources(raw)
    else:
        # Default: Shazam, plus YouTube whenever a key is configured.
        sources = ["shazam"] + (["youtube"] if key else [])

    if "youtube" in sources and not key:
        raise ConfigError(
            "YouTube statistics were requested but YOUTUBE_API_KEY is not set. "
            "Add it to .env (see .env.example) or run with --no-youtube.")
    return Config(sources=sources, youtube_api_key=key)
