"""Caregiver alerts through a Telegram bot (free). Sent straight from the laptop, so an alert
still arrives even if the online dashboard is asleep.

One-time setup:
  1. In Telegram, message @BotFather → /newbot → follow the steps → copy the token into .env:
         TELEGRAM_BOT_TOKEN=123456:ABC...
  2. Open your new bot in Telegram and send it any message (e.g. "hi").
     (For a group: add the bot to the group and send a message there.)
  3. Find the chat id and copy it into .env as TELEGRAM_CHAT_ID:
         python -m basestation.alerts.telegram --find-chat-id
  4. Send a test alert:
         python -m basestation.alerts.telegram --test

This is a demo alert to a teammate's phone. It never contacts emergency services.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
API = "https://api.telegram.org/bot{token}/{method}"


def _token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def is_configured() -> bool:
    return bool(_token() and os.environ.get("TELEGRAM_CHAT_ID", "").strip())


def send_alert(text: str, timeout_s: float = 5) -> tuple[bool, str]:
    """Send one message. Returns (sent?, reason). Never raises, never prints the token."""
    if not is_configured():
        return False, "Telegram not set up (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID in .env)"
    try:
        r = requests.post(API.format(token=_token(), method="sendMessage"),
                          data={"chat_id": os.environ["TELEGRAM_CHAT_ID"].strip(), "text": text},
                          timeout=timeout_s)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.ok and body.get("ok"):
            return True, "sent"
        return False, f"Telegram said: {body.get('description', f'HTTP {r.status_code}')}"
    except requests.RequestException as e:
        # only the error type: the message would contain the URL, which contains the token
        return False, f"can't reach Telegram ({type(e).__name__})"


def find_chat_ids() -> list[tuple[str, str]]:
    r = requests.get(API.format(token=_token(), method="getUpdates"), timeout=10)
    chats = {}
    for update in r.json().get("result", []):
        msg = update.get("message") or update.get("channel_post") or {}
        chat = msg.get("chat")
        if chat:
            name = chat.get("title") or " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")]))
            chats[str(chat["id"])] = name
    return list(chats.items())


def main() -> None:
    parser = argparse.ArgumentParser(description="SilverGuard Telegram alerts")
    parser.add_argument("--find-chat-id", action="store_true", help="list chats that messaged the bot")
    parser.add_argument("--test", action="store_true", help="send a test alert")
    args = parser.parse_args()

    if not _token():
        print("No TELEGRAM_BOT_TOKEN in .env yet. See the steps at the top of this file.")
        return
    if args.find_chat_id:
        chats = find_chat_ids()
        if not chats:
            print("No messages found. Send your bot a message in Telegram first, then run this again.")
        for chat_id, name in chats:
            print(f"TELEGRAM_CHAT_ID={chat_id}    ({name})")
    if args.test:
        print(send_alert("✅ SilverGuard test alert. If you can read this, caregiver alerts work."))


if __name__ == "__main__":
    main()
