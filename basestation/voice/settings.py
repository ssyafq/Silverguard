"""Loads the `voice:` section of basestation/config.yaml and the API key from .env.

All voice timings, model names and spoken lines live in config.yaml, not in code,
so the team can tune them without touching Python.
"""

from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "basestation" / "config.yaml"
AUDIO_CACHE_DIR = PROJECT_ROOT / "basestation" / "voice" / "audio_cache"
LOG_PATH = PROJECT_ROOT / "data" / "raw" / "voice_checkins.csv"   # data/raw is git-ignored

# Puts ANTHROPIC_API_KEY from .env into the environment (if .env exists).
load_dotenv(PROJECT_ROOT / ".env")


@lru_cache(maxsize=1)
def _config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def voice_config() -> dict:
    return _config()["voice"]


def dashboard_config() -> dict:
    return _config()["dashboard"]
