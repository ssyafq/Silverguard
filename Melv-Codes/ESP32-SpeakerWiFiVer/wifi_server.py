"""HTTP Voice Check-In Server for ESP32-SpeakerWiFiVer (runs locally or on Railway).

Endpoints:
  GET  /health         -> {"status": "ok"}
  GET  /prompt/<name>  -> 16 kHz 16-bit mono PCM for 'question', 'ok', or 'alert' (EN + ZH)
  POST /checkin        -> accepts 16 kHz 16-bit mono PCM body from the ESP32 INMP441 mic,
                          runs Whisper STT -> Claude (or keyword backup) -> fixed rules ->
                          Supabase + Telegram alert if needed, and returns 16 kHz 16-bit mono
                          PCM spoken response with headers:
                              X-Reply: ok | help | none
                              X-Next:  done | retry

Usage (local laptop test, same Wi-Fi as ESP32):
    python Melv-Codes/ESP32-SpeakerWiFiVer/wifi_server.py --port 8000

Usage (Railway):
    Start command: python Melv-Codes/ESP32-SpeakerWiFiVer/wifi_server.py
    (reads the PORT environment variable automatically)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import soundfile as sf

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from basestation.alerts import telegram  # noqa: E402
from basestation.sync.uploader import Cloud, new_id, now_iso  # noqa: E402
from basestation.voice import listen, speak, understand  # noqa: E402
from basestation.voice.settings import dashboard_config, voice_config  # noqa: E402

AUDIO_RATE = 16_000
_pcm_cache: dict[str, bytes] = {}
_cloud: Cloud | None = None


def _resample_to_16k_mono_pcm16(data: np.ndarray, samplerate: int) -> bytes:
    audio = np.asarray(data, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if samplerate != AUDIO_RATE and audio.size > 0:
        new_len = int(round((audio.size / float(samplerate)) * AUDIO_RATE))
        if new_len > 0:
            old_idx = np.linspace(0.0, 1.0, num=audio.size, endpoint=False)
            new_idx = np.linspace(0.0, 1.0, num=new_len, endpoint=False)
            audio = np.interp(new_idx, old_idx, audio).astype(np.float32)
    return (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def get_prompt_pcm(name: str) -> bytes:
    """Return 16 kHz 16-bit mono PCM bytes for a scripted line (EN + 0.3s pause + ZH)."""
    if name in _pcm_cache:
        return _pcm_cache[name]

    lines = voice_config()["lines"]
    if name not in lines:
        return b""

    parts = []
    pause = np.zeros(int(AUDIO_RATE * 0.3), dtype="<i2").tobytes()
    for lang in ("en", "zh"):
        text = lines[name][lang]
        speak.prepare(text, lang)
        voice = speak._voice_for(lang)
        mp3_path = speak._cache_path(text, voice)
        if mp3_path.exists():
            data, sr = sf.read(str(mp3_path), dtype="float32")
            parts.append(_resample_to_16k_mono_pcm16(data, sr))
            parts.append(pause)

    pcm = b"".join(parts)
    _pcm_cache[name] = pcm
    return pcm


def send_caregiver_alert(why: str, heard: str, event_id: str) -> None:
    dash = dashboard_config()
    text = (
        f"⚠️ SilverGuard: possible fall at {datetime.now():%H:%M}, {why}."
        + (f" They said: “{heard}”." if heard else "")
        + f"\nPlease check on {dash['elder_name']}."
        + (f"\nDashboard: {dash['url']}" if dash.get("url") else "")
    )
    sent, info = telegram.send_alert(text)
    if _cloud is not None:
        _cloud.update("events", event_id, {"alerted_at": now_iso()})
        _cloud.step(
            event_id,
            "Caregiver alerted on Telegram" if sent else f"Telegram not sent ({info}); dashboard only",
        )
    print(f"  [alert] {why} -> Telegram: {'SENT' if sent else info}")


class WatchHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        print(f"[{datetime.now():%H:%M:%S}] {self.address_string()} {fmt % args}")

    def _send_bytes(self, code: int, body: bytes, content_type: str, extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_bytes(200, json.dumps({"status": "ok"}).encode(), "application/json")
            return

        if parsed.path.startswith("/prompt/"):
            name = parsed.path.split("/prompt/", 1)[1].strip("/")
            pcm = get_prompt_pcm(name)
            if not pcm:
                self._send_bytes(404, b"unknown prompt", "text/plain")
                return
            self._send_bytes(200, pcm, "application/octet-stream")
            return

        self._send_bytes(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/checkin":
            self._send_bytes(404, b"not found", "text/plain")
            return

        t0 = time.perf_counter()
        qs = parse_qs(parsed.query)
        device_id = qs.get("device_id", ["watch-01"])[0]
        attempt = int(qs.get("attempt", ["1"])[0])
        score = float(qs.get("score", ["0.0"])[0])

        content_len = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(content_len) if content_len > 0 else b""
        # Ensure even byte count for int16
        raw = raw[: len(raw) - (len(raw) % 2)]
        audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0

        rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
        if listen.is_silent(audio):
            transcript, lang = "", ""
        else:
            transcript, lang = listen.transcribe(audio)

        result = understand.classify(transcript, reason="fall")
        reply = result.reply
        max_attempts = 1 + int(voice_config()["retries"])

        # Fixed rules engine (LLM never decides the alert)
        if reply == "ok":
            next_action = "done"
            prompt_name = "ok"
        elif reply == "help":
            next_action = "done"
            prompt_name = "alert"
        elif attempt < max_attempts:
            next_action = "retry"
            prompt_name = "question"
        else:
            next_action = "done"
            prompt_name = "alert"

        # Log to Supabase & send Telegram alert if needed
        event_id = new_id()
        if _cloud is not None:
            _cloud.watch_last_data_at = now_iso()
            _cloud.insert("events", {
                "id": event_id, "t": now_iso(), "type": "fall",
                "detector": f"ESP32-SpeakerWiFiVer ({device_id}, score={score:.2f})",
                "status": "false_alarm" if reply == "ok" else "open",
                "resolved_at": now_iso() if reply == "ok" else None,
            })
            _cloud.insert("checkins", {
                "id": new_id(), "event_id": event_id, "t": now_iso(), "attempt": attempt,
                "transcript": transcript, "reply": reply, "used_fallback": result.used_fallback,
                "latency_s": round(time.perf_counter() - t0, 2), "llm_s": round(result.llm_s, 2) or None,
            })
            _cloud.step(event_id, f"Watch Wi-Fi check-in attempt {attempt}: heard “{transcript}” -> {reply}")

        if prompt_name == "alert":
            why = "asked for help" if reply == "help" else "no reply to check-in"
            send_caregiver_alert(why, transcript, event_id)

        response_pcm = get_prompt_pcm(prompt_name)
        print(
            f"  [checkin] device={device_id} attempt={attempt} rms={rms:.4f} "
            f"heard={transcript!r} ({lang}) -> reply={reply} next={next_action} "
            f"({time.perf_counter() - t0:.1f}s)"
        )
        self._send_bytes(
            200,
            response_pcm,
            "application/octet-stream",
            extra_headers={"X-Reply": reply, "X-Next": next_action},
        )


def _warmup() -> None:
    """Pre-generate TTS prompts and load Whisper in the background after the HTTP port is bound."""
    try:
        print("Warming up: pre-generating 16 kHz PCM prompts (question, ok, alert)...")
        speak.prepare_all_lines()
        for name in ("question", "ok", "alert"):
            pcm = get_prompt_pcm(name)
            print(f"  prompt/{name}: {len(pcm)} bytes ({len(pcm) / (AUDIO_RATE * 2):.1f} s)")
        print("Warming up: loading Whisper STT model...")
        listen.load_model()
        print("Warmup complete! Ready for check-ins.")
    except Exception as exc:
        print(f"Warmup warning: {exc}")


def main() -> None:
    global _cloud
    parser = argparse.ArgumentParser(description="SilverGuard Wi-Fi voice check-in server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")), help="Port (default 8000 or $PORT)")
    args = parser.parse_args()

    _cloud = Cloud()
    print("Online dashboard:", "ON (Supabase)" if _cloud.enabled else "OFF")
    print("Telegram alerts: ", "ON" if telegram.is_configured() else "OFF")

    # Bind the HTTP port immediately so Cloud Run's startup probe succeeds in <1 s
    server = ThreadingHTTPServer((args.host, args.port), WatchHandler)
    print(f"\nSilverGuard Wi-Fi Server listening on http://{args.host}:{args.port}")
    threading.Thread(target=_warmup, daemon=True).start()
    print("Waiting for ESP32-SpeakerWiFiVer check-ins (Ctrl+C to stop)...\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        _cloud.close()


if __name__ == "__main__":
    main()
