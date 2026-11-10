"""Tests for the voice 'brain' (ok / help / none).

    python -m pytest tests/test_understand.py -v

tests/voice_phrases.csv is the scripted reply set. Rows marked hard=1 are ones only the
LLM is expected to get right (the keyword backup is too simple). The same file becomes
the Obj 4 dialogue-error test set — add real phrases from teammates and trials to it.
"""

import csv
import os
from pathlib import Path

import pytest

from basestation.voice import understand   # also loads .env
from basestation.voice.keywords import classify_keywords

with open(Path(__file__).with_name("voice_phrases.csv"), encoding="utf-8") as f:
    PHRASES = list(csv.DictReader(f))
EASY = [p for p in PHRASES if p["hard"] == "0"]


def _id(p):
    return f'{p["expected"]}:{p["text"] or "<silence>"}'


@pytest.mark.parametrize("p", EASY, ids=_id)
def test_keyword_backup(p):
    assert classify_keywords(p["text"]) == p["expected"]


def test_silence_skips_llm(monkeypatch):
    def must_not_call(*args, **kwargs):
        raise AssertionError("LLM should not be called for an empty reply")
    monkeypatch.setattr(understand, "_ask_llm", must_not_call)
    result = understand.classify("   ")
    assert result.reply == "none" and not result.used_fallback


def test_llm_failure_falls_back_to_keywords(monkeypatch):
    def offline(*args, **kwargs):
        raise ConnectionError("simulated: no internet")
    monkeypatch.setattr(understand, "_ask_llm", offline)
    result = understand.classify("help me, 好痛")
    assert result.reply == "help"
    assert result.used_fallback and "no internet" in result.fallback_reason


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="no ANTHROPIC_API_KEY in .env")
@pytest.mark.parametrize("p", PHRASES, ids=_id)
def test_llm(p):
    result = understand.classify(p["text"])
    assert not result.used_fallback, result.fallback_reason
    assert result.reply == p["expected"]
