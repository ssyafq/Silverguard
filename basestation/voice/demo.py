"""Demo: type an event (e.g. "fall") and the voice check-in runs.

    python -m basestation.voice.demo            # speak your reply into the mic
    python -m basestation.voice.demo --typed    # type your reply instead (no mic needed)

If set up in .env, it also:
  * writes the event, each check-in and each step to the online database → shows on the dashboard
  * sends the caregiver alert to Telegram
Without those keys it still works, locally only.

The retry/alert logic below is a SIMPLE STAND-IN for the real rules engine (Obj 3,
basestation/rules/). The LLM never decides the alert — these fixed rules do:
    ok   → no alert
    help → alert straight away
    none → ask once more; still none → alert
Alerts go to a teammate's phone. Nothing is ever sent to emergency services.
"""

import argparse
import time
from datetime import datetime

from basestation.alerts import telegram
from basestation.sync.uploader import Cloud, new_id, now_iso
from basestation.voice import speak
from basestation.voice.checkin import check_in
from basestation.voice.settings import dashboard_config, voice_config

TITLES = {"fall": "Possible fall", "inactivity": "Long inactivity"}


def handle_event(reason: str, typed: bool, cloud: Cloud) -> str:
    cfg = voice_config()
    max_attempts = 1 + int(cfg["retries"])
    budget_s = float(cfg["check_in_total_timeout_s"])
    title = TITLES.get(reason, reason.capitalize())
    start = time.perf_counter()

    event_id = new_id()
    cloud.insert("events", {"id": event_id, "t": now_iso(), "type": reason, "detector": "typed demo", "status": "open"})
    cloud.step(event_id, f"{title} reported (typed in the demo)")

    for attempt in range(1, max_attempts + 1):
        print(f"\n[check-in] reason={reason} attempt={attempt}")
        cloud.step(event_id, "Base station asked “Are you okay?”" if attempt == 1 else "No clear reply, asked again")
        rec = check_in(reason, attempt, typed=typed)
        cloud.insert("checkins", {
            "id": new_id(), "event_id": event_id, "t": now_iso(), "attempt": attempt,
            "transcript": rec["transcript"], "reply": rec["reply"], "used_fallback": rec["used_fallback"],
            "latency_s": rec["total_s"], "llm_s": rec["llm_s"] or None,
        })
        heard = rec["transcript"]
        cloud.step(event_id, f"Heard “{heard}” → {rec['reply']}" if heard else "No reply heard")

        if rec["reply"] == "ok":
            speak.say_line("ok")
            cloud.update("events", event_id, {"status": "false_alarm" if reason == "fall" else "resolved",
                                              "resolved_at": now_iso()})
            cloud.step(event_id, "No alert: they said they're OK")
            print("[rules] User says they are OK → no alert.")
            return "no_alert"
        if rec["reply"] == "help":
            return alert(cloud, event_id, title, "asked for help", heard)

        elapsed = time.perf_counter() - start
        if attempt >= max_attempts or elapsed >= budget_s:
            break
        print(f"[rules] No clear reply ({elapsed:.0f}s used of {budget_s:.0f}s) → asking again.")

    return alert(cloud, event_id, title, "no reply to check-in", "")


def alert(cloud: Cloud, event_id: str, title: str, why: str, heard: str) -> str:
    speak.say_line("alert")
    dash = dashboard_config()
    text = (f"⚠️ SilverGuard: {title.lower()} at {datetime.now():%H:%M}, {why}."
            + (f" They said: “{heard}”." if heard else "")
            + f"\nPlease check on {dash['elder_name']}."
            + (f"\nDashboard: {dash['url']}" if dash.get("url") else ""))
    sent, info = telegram.send_alert(text)
    cloud.update("events", event_id, {"alerted_at": now_iso()})
    cloud.step(event_id, "Caregiver alerted on Telegram" if sent else f"Telegram alert not sent ({info}); dashboard only")
    print(f"[rules] {why} → ALERT. " + ("Telegram message sent." if sent else f"Telegram NOT sent: {info}."))
    return "alert_help" if why == "asked for help" else "alert_no_reply"


def main() -> None:
    parser = argparse.ArgumentParser(description="SilverGuard voice check-in demo")
    parser.add_argument("--typed", action="store_true", help="type the reply instead of using the mic")
    args = parser.parse_args()

    cloud = Cloud()
    print("Online dashboard:", "ON (Supabase)" if cloud.enabled else "OFF (add SUPABASE_URL + SUPABASE_SECRET_KEY to .env)")
    print("Telegram alerts: ", "ON" if telegram.is_configured() else "OFF (see basestation/alerts/telegram.py)")

    print("Getting ready: making the spoken lines (needs internet the first time)...")
    speak.prepare_all_lines()
    if not args.typed:
        print("Loading the speech-to-text model (first run downloads it, ~0.5 GB for 'small')...")
        from basestation.voice import listen
        listen.load_model()

    print("\nReady. Type an event like 'fall' (or 'quit').")
    try:
        while True:
            event = input("\nevent> ").strip().lower()
            if event in ("quit", "exit", "q"):
                break
            if event:
                outcome = handle_event(event, typed=args.typed, cloud=cloud)
                print(f"[done] outcome={outcome}")
    finally:
        cloud.close()


if __name__ == "__main__":
    main()
