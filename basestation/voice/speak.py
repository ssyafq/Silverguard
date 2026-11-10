"""Mouth: turns text into speech and plays it on the speaker.

Main path: edge-tts (free online neural voices, incl. Singapore English and Mandarin).
Audio is cached as MP3, so fixed lines like the check-in question play instantly
after the first time — and still play when the internet is down.
Backup path: pyttsx3, the Windows built-in voice (offline, more robotic).
"""

import asyncio
import hashlib
import time

import sounddevice as sd
import soundfile as sf

from basestation.voice.settings import AUDIO_CACHE_DIR, voice_config


def _voice_for(lang: str) -> str:
    cfg = voice_config()
    return cfg["tts_voice_zh"] if lang == "zh" else cfg["tts_voice_en"]


def _cache_path(text: str, voice: str):
    key = hashlib.sha1(f"{voice}|{text}".encode("utf-8")).hexdigest()[:16]
    return AUDIO_CACHE_DIR / f"{key}.mp3"


def _synthesise_edge(text: str, voice: str, path) -> None:
    import edge_tts

    async def _run():
        await edge_tts.Communicate(text, voice).save(str(path))

    AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(asyncio.wait_for(_run(), timeout=10))
    except BaseException:
        path.unlink(missing_ok=True)       # don't leave a half-written file in the cache
        raise


def _play_file(path) -> None:
    data, samplerate = sf.read(str(path), dtype="float32")
    sd.play(data, samplerate)
    sd.wait()


def _speak_offline(text: str, lang: str) -> None:
    import pyttsx3

    engine = pyttsx3.init()
    if lang == "zh":
        # Only works if a Chinese Windows voice is installed (Settings → Time & language → Speech).
        zh = [v for v in engine.getProperty("voices")
              if "chinese" in v.name.lower() or "zh" in v.id.lower()]
        if not zh:
            engine.stop()
            raise RuntimeError("no offline Chinese voice installed")
        engine.setProperty("voice", zh[0].id)
    engine.say(text)
    engine.runAndWait()
    engine.stop()


def prepare(text: str, lang: str = "en") -> bool:
    """Make sure a line is in the audio cache. Returns True if it is."""
    voice = _voice_for(lang)
    path = _cache_path(text, voice)
    if not path.exists():
        try:
            _synthesise_edge(text, voice, path)
        except Exception:
            return False
    return True


def speak(text: str, lang: str = "en") -> dict:
    """Say `text` out loud. Returns which engine was used and how long it took.

    Never raises: if both engines fail, it returns engine="failed" so the check-in can
    carry on (and the log shows the problem).
    """
    start = time.perf_counter()
    voice = _voice_for(lang)
    path = _cache_path(text, voice)
    try:
        if not path.exists():
            _synthesise_edge(text, voice, path)
        _play_file(path)
        engine = "edge-tts"
    except Exception:
        try:
            _speak_offline(text, lang)
            engine = "pyttsx3"
        except Exception:
            engine = "failed"
    return {"engine": engine, "seconds": time.perf_counter() - start}


def say_line(name: str) -> dict:
    """Say one of the scripted lines from config.yaml (voice.lines) in English, then Mandarin."""
    line = voice_config()["lines"][name]
    en = speak(line["en"], "en")
    zh = speak(line["zh"], "zh")
    engines = en["engine"] if en["engine"] == zh["engine"] else f'{en["engine"]}+{zh["engine"]}'
    return {"engine": engines, "seconds": en["seconds"] + zh["seconds"]}


def prepare_all_lines() -> None:
    """Pre-make audio for every scripted line, so check-ins don't wait for the internet."""
    for line in voice_config()["lines"].values():
        prepare(line["en"], "en")
        prepare(line["zh"], "zh")


if __name__ == "__main__":
    print(say_line("question"))
