"""Responsible AI guardrails: first-turn disclosure, polite opt-in reminders & DPDP wipe."""

from __future__ import annotations

import re
import sqlite3

from app.schemas.models import LanguageCode


AI_DISCLOSURES: dict[str, str] = {
    "hi-IN": "Namaste! Main Voice Khata AI assistant hoon, jo aapki dukaan ke udhaar aur payment hisaab mein madad karta hai.",
    "kn-IN": "ನಮಸ್ಕಾರ! ನಾನು Voice Khata AI ಅಸಿಸ್ಟೆಂಟ್. ನಿಮ್ಮ ಅಂಗಡಿಯ ಉದಾರ್ ಮತ್ತು ಪೇಮೆಂಟ್ ಲೆಕ್ಕಗಳನ್ನು ದಾಖಲಿಸಲು ಸಹಾಯ ಮಾಡುತ್ತೇನೆ.",
    "ta-IN": "Vanakkam! Naan Voice Khata AI assistant. Ungal kadaiyin kadan matrum varavu kanakku parkka udhavuven.",
    "te-IN": "Namaskaram! Nenu Voice Khata AI assistant. Mee dukanam udhaar mariyu payment lekhalu record cheyadaniki sahayam chestanu.",
    "en-IN": "Hello! I am the Voice Khata AI assistant, here to help record your shop credit and payment ledger.",
}

# Coercive, pressure, or lending terms forbidden in customer reminders
_DISALLOWED_REMINDER_TERMS = re.compile(
    r"\b(threat|police|court|legal|action|urgent|warning|penalty|interest|loan|borrow|emi|fine|must pay immediately)\b",
    re.IGNORECASE,
)


def get_ai_disclosure(language: LanguageCode | str) -> str:
    """Return the first-turn AI identity disclosure in the user's language."""
    return AI_DISCLOSURES.get(language, AI_DISCLOSURES["hi-IN"])


def build_polite_reminder(customer_name: str, balance: float, language: LanguageCode | str) -> str:
    """Generate a polite, zero-pressure payment reminder strictly for opted-in customers."""
    amt = int(round(balance)) if abs(balance - round(balance)) < 1e-6 else round(balance, 2)
    templates = {
        "hi-IN": (
            f"Namaste {customer_name} ji, aapka khata balance ₹{amt} hai. "
            "Kripya apni suvidha ke anusaar check kar lijiye. Dhanyavaad."
        ),
        "kn-IN": (
            f"ನಮಸ್ಕಾರ {customer_name} ಅವರೇ, ನಿಮ್ಮ ಖಾತೆ ಬಾಕಿ ₹{amt} ಇದೆ. "
            "ದಯವಿಟ್ಟು ನಿಮ್ಮ ಅನುಕೂಲಕ್ಕೆ ತಕ್ಕಂತೆ ಪರಿಶೀಲಿಸಿ. ಧನ್ಯವಾದಗಳು."
        ),
        "ta-IN": (
            f"Vanakkam {customer_name} avargale, ungal khata meedhi ₹{amt} ulladhu. "
            "Ungal vasadhikketra nerathil sariparkkavum. Nandri."
        ),
        "te-IN": (
            f"Namaskaram {customer_name} garu, mee khata balance ₹{amt} undi. "
            "Dayachesi mee veelu batti chusukondi. Dhanyavaadalu."
        ),
        "en-IN": (
            f"Namaste {customer_name} ji, your khata balance is ₹{amt}. "
            "Please check your account when convenient. Thank you."
        ),
    }
    msg = templates.get(language, templates["hi-IN"])
    if _DISALLOWED_REMINDER_TERMS.search(msg):
        raise ValueError("Reminder failed Responsible AI politeness validation.")
    return msg


def wipe_demo_transcripts(target: object) -> int:
    """DPDP compliance: scrub stored spoken utterances/replies from voice_logs and transactions."""
    if isinstance(target, sqlite3.Connection):
        cur = target.execute("DELETE FROM voice_logs")
        deleted = cur.rowcount
        target.execute("UPDATE transactions SET raw_utterance_enc = NULL WHERE raw_utterance_enc IS NOT NULL")
        target.execute("UPDATE voice_sessions SET pending_action_json = NULL, last_committed_tx_ids_json = NULL")
        return deleted
    if hasattr(target, "wipe_demo_transcripts"):
        return int(target.wipe_demo_transcripts())
    raise TypeError(f"Unsupported wipe target: {type(target)!r}")

