"""One voice check-in: ask → listen → understand. Returns the §4 interface contract.

    → run_check_in(reason="fall", attempt=1)
    ← {"reply": "ok" | "help" | "none", "transcript": "...", "latency_s": 12.3}

Every check-in is also logged as one CSV row (evidence for Obj 4: dialogue errors and
latency). Only the transcript is stored — never the audio.
"""

import csv
import time
from datetime import datetime

from basestation.voice import speak
from basestation.voice.settings import LOG_PATH
from basestation.voice.understand import classify

LOG_COLUMNS = [
    "time", "reason", "attempt", "mode", "transcript", "language", "reply",
    "used_fallback", "fallback_reason", "tts_engine", "tts_s", "record_s", "stt_s", "llm_s", "total_s",
]


def _log(row: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    new_file = not LOG_PATH.exists()
    with open(LOG_PATH, "a", newline="", encoding="utf-8-sig") as f:   # utf-8-sig: Excel shows Chinese correctly
        writer = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
        if new_file:
            writer.writeheader()
        writer.writerow(row)


def run_check_in(reason: str = "fall", attempt: int = 1, typed: bool = False) -> dict:
    """§4 interface contract: one attempt → {"reply", "transcript", "latency_s"}."""
    row = check_in(reason, attempt, typed)
    return {"reply": row["reply"], "transcript": row["transcript"], "latency_s": row["total_s"]}


def check_in(reason: str = "fall", attempt: int = 1, typed: bool = False) -> dict:
    """Run one check-in attempt and return the full record (the same fields as the CSV log).

    typed=True: instead of the microphone, the reply is typed on the keyboard
    (for testing without a mic, or to test the brain on exact phrases).
    """
    start = time.perf_counter()

    # 1. MOUTH — ask the question (English, then Mandarin)
    tts = speak.say_line("question")

    # 2. EARS — get the reply as text
    if typed:
        heard = {"transcript": input("  Type the reply (Enter = silence): ").strip(),
                 "language": "", "record_s": 0.0, "stt_s": 0.0}
    else:
        from basestation.voice import listen     # imported here so typed mode doesn't need Whisper
        print("  Listening...")
        heard = listen.listen()

    # 3. BRAIN — sort the reply into ok / help / none
    result = classify(heard["transcript"], reason)

    total = time.perf_counter() - start
    row = {
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "reason": reason, "attempt": attempt, "mode": "typed" if typed else "voice",
        "transcript": heard["transcript"], "language": heard["language"], "reply": result.reply,
        "used_fallback": result.used_fallback, "fallback_reason": result.fallback_reason,
        "tts_engine": tts["engine"], "tts_s": round(tts["seconds"], 2),
        "record_s": round(heard["record_s"], 2), "stt_s": round(heard["stt_s"], 2),
        "llm_s": round(result.llm_s, 2), "total_s": round(total, 2),
    }
    _log(row)

    backup = f"  [keyword backup: {result.fallback_reason}]" if result.used_fallback else ""
    print(f"  Heard: {heard['transcript']!r} → {result.reply}{backup}")
    print(f"  Time: speak {tts['seconds']:.1f}s | record {heard['record_s']:.1f}s | "
          f"STT {heard['stt_s']:.1f}s | LLM {result.llm_s:.1f}s | total {total:.1f}s")
    return row
