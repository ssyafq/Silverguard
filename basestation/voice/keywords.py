"""Offline backup brain: sorts a reply into ok / help / none by looking for keywords.

Used when the cloud LLM can't be reached (no internet, timeout, API error).
It is deliberately cautious:
  * "help" words are checked BEFORE "ok" words, so "not okay" or "好痛" become help.
  * Anything with no recognisable word becomes "none" (unclear), which leads to a
    retry and then an alert — never a silent "everything is fine".
"""

import re

# Whisper often writes Chinese in traditional characters; map the ones we need to simplified.
_TRAD_TO_SIMP = str.maketrans({
    "沒": "没", "幫": "帮", "醫": "医", "頭": "头", "暈": "晕", "護": "护", "車": "车",
    "來": "来", "問": "问", "題": "题", "關": "关", "係": "系", "緊": "紧", "還": "还",
    "對": "对", "嗎": "吗", "謝": "谢", "舒": "舒", "倒": "倒", "傷": "伤", "動": "动",
})

# Phrases that sound like help but mean "I'm fine". Removed before the help check.
OK_PHRASES = [
    "no problem", "no worries", "don't worry", "dont worry", "not hurt", "no pain",
    "nothing wrong", "no need", "not bad",
    "没问题", "不用担心", "不痛", "不疼", "没有痛", "没受伤", "不要紧", "没关系", "不用",
]

HELP_WORDS_EN = [
    "help", "pain", "painful", "hurt", "hurts", "fell", "fallen", "fall", "injured",
    "can't", "cant", "cannot", "can not", "unable", "not ok", "not okay", "not fine",
    "not good", "not alright", "not really", "ambulance", "doctor", "bleeding", "blood",
    "dizzy", "giddy", "faint", "emergency", "call", "tolong", "sakit",   # last two: Malay
]
HELP_WORDS_ZH = [
    "救命", "帮", "痛", "疼", "不好", "不太好", "不是很好", "不舒服", "不太舒服", "起不来",
    "站不起", "跌倒", "摔", "头晕", "医生", "救护车", "不行", "受伤", "流血", "不能动",
]

# Weak help: "no" on its own answers "Are you okay?" with no — but "No, no, I'm fine" is ok.
# So these only count as help when no ok word is present.
WEAK_HELP_EN = ["no", "nope", "not"]
WEAK_HELP_ZH = ["不是", "没有"]

OK_WORDS_EN = [
    "yes", "yeah", "yep", "ok", "okay", "fine", "alright", "all right", "good", "i'm ok",
    "safe", "no problem", "can",   # "can" = Singlish "I'm able / it's fine"
]
OK_WORDS_ZH = ["没事", "没有事", "还好", "很好", "好", "是", "对", "可以", "没问题", "不要紧", "没关系", "不痛", "不疼"]


def _normalise(text: str) -> str:
    text = text.translate(_TRAD_TO_SIMP).lower()
    text = text.replace("’", "'")
    return " ".join(text.split())


def _has_en(text: str, words: list[str]) -> bool:
    # Whole-word match so "fine" doesn't match inside "define".
    # (?!') stops "can" matching the start of "can't".
    return any(re.search(rf"(?<![\w']){re.escape(w)}(?![\w'])", text) for w in words)


def _has_zh(text: str, words: list[str]) -> bool:
    return any(w in text for w in words)


def classify_keywords(transcript: str) -> str:
    """Return "ok", "help" or "none" for one transcript."""
    text = _normalise(transcript)
    if not re.search(r"\w", text):
        return "none"                       # empty, or only punctuation/noise

    ok_by_phrase = any(p in text for p in OK_PHRASES)
    for p in OK_PHRASES:                    # "no problem" must not trigger the help word "no"
        text = text.replace(p, " ")

    if _has_en(text, HELP_WORDS_EN) or _has_zh(text, HELP_WORDS_ZH):
        return "help"                       # safety first: any strong help word wins
    if ok_by_phrase or _has_en(text, OK_WORDS_EN) or _has_zh(text, OK_WORDS_ZH):
        return "ok"
    if _has_en(text, WEAK_HELP_EN) or _has_zh(text, WEAK_HELP_ZH):
        return "help"                       # a bare "no" / "不是"
    return "none"                           # words, but nothing we recognise → unclear
