"""Brain: sorts the transcript of a spoken reply into ok / help / none.

Main path: a cloud LLM (Claude, model set in config.yaml) with structured output, so
the answer can only ever be one of the three labels.
Backup path: keyword matching (keywords.py) if the LLM can't be reached or fails.

The LLM ONLY interprets the reply. Whether to alert is decided by the rules engine.
"""

import os
import time
from dataclasses import dataclass
from typing import Literal

import anthropic
from pydantic import BaseModel

from basestation.voice.keywords import classify_keywords
from basestation.voice.settings import voice_config

SYSTEM_PROMPT = """\
You sort an older adult's spoken reply during a safety check-in.
A wrist sensor flagged a possible problem (the reason is given), and a speaker asked them
"Are you okay?" in English and Mandarin. Their reply was turned into text by speech
recognition, so it may contain recognition mistakes. It may be in English, Mandarin,
Singlish, Malay, or a mix, in simplified or traditional Chinese.

Choose exactly one label:
- ok: they say they are fine, not hurt, or don't need help.
- help: they ask for help; say they are hurt, in pain, dizzy, or can't get up; answer "no"
  to "are you okay"; or sound distressed or confused.
- none: the text is empty, only noise or filler words, or unrelated, giving no sign either way.

Safety rules:
- If unsure between ok and help, choose help. A missed emergency is worse than a false alarm.
- Mixed messages ("I'm okay but my leg hurts", "没事, 就是起不来") are help.
- "No, no, I'm fine" is ok: the "no" means "no problem".

Examples:
"yes I'm okay" -> ok | "我没事" -> ok | "can, can, no need" -> ok | "I'm fine lah" -> ok
"help me" -> help | "好痛啊" -> help | "I cannot get up" -> help | "no" -> help | "头晕, dizzy" -> help
"" -> none | "uh... hmm" -> none | "thank you for watching" -> none
"""


class Reply(BaseModel):
    label: Literal["ok", "help", "none"]


@dataclass
class Classification:
    reply: str                  # "ok" | "help" | "none"
    used_fallback: bool         # True if keyword backup was used instead of the LLM
    fallback_reason: str        # why the backup was used ("" if it wasn't)
    llm_s: float                # seconds spent in this step


_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        cfg = voice_config()
        # max_retries=0: an older person is waiting, so on any failure we use the keyword
        # backup straight away instead of retrying and making them wait longer.
        _client = anthropic.Anthropic(timeout=float(cfg["llm_timeout_s"]), max_retries=0)
    return _client


def _ask_llm(transcript: str, reason: str) -> str:
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise RuntimeError("no API key: copy .env.example to .env and add ANTHROPIC_API_KEY")
    cfg = voice_config()
    response = _get_client().messages.parse(
        model=cfg["llm_model"],
        max_tokens=1024,                                  # room for short thinking + the label
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"Reason for check-in: {reason}\nReply transcript: <<<{transcript}>>>",
        }],
        output_config={"effort": cfg["llm_effort"]},
        output_format=Reply,
    )
    if response.stop_reason != "end_turn" or response.parsed_output is None:
        raise ValueError(f"unusable LLM answer (stop_reason={response.stop_reason})")
    return response.parsed_output.label


def classify(transcript: str, reason: str = "fall") -> Classification:
    """Sort one transcript. Never raises: on any LLM problem it uses the keyword backup."""
    start = time.perf_counter()

    if not transcript.strip():
        # Nothing was heard: no need to ask the LLM (saves time and money).
        return Classification("none", False, "", 0.0)

    try:
        label = _ask_llm(transcript, reason)
        return Classification(label, False, "", time.perf_counter() - start)
    except anthropic.AuthenticationError:
        why = "API key missing or wrong (check .env)"
    except anthropic.APITimeoutError:
        why = "LLM timed out"
    except anthropic.APIConnectionError:
        why = "no internet / can't reach LLM"
    except anthropic.RateLimitError:
        why = "rate limited"
    except anthropic.APIStatusError as e:
        why = f"LLM API error {e.status_code}"
    except Exception as e:  # last resort: a safety check-in must never crash
        why = f"{type(e).__name__}: {e}"

    return Classification(classify_keywords(transcript), True, why, time.perf_counter() - start)


if __name__ == "__main__":
    # Quick manual test: type replies and see how they are sorted.
    print("Type a reply (empty line to quit).")
    while (text := input("> ")).strip():
        print(classify(text))
