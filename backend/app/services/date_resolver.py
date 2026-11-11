"""Relative Indic + English date resolver for ledger transactions."""

from __future__ import annotations

import re
from datetime import date, timedelta


_PAST_MARKERS = (
    "tha", "thi", "the", "diya", "diye", "liya", "liye", "gaya", "aaya", "kiya",
    "paid", "returned", "yesterday", "ninne", "monne", "netru", "kottidane",
    "madidane", "kuduthaan", "icharu", "le gaya", "bheja", "jama",
)


def resolve_date_phrase(phrase: str | None, utterance_context: str = "", ref_date: date | None = None) -> str:
    """Resolve relative Indic/English date phrases ('aaj', 'kal', 'parso', 'ninne', 'netru') to ISO dates."""
    today = ref_date or date.today()
    if not phrase:
        return today.isoformat()

    p = phrase.strip().lower()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p):
        return p

    ctx = utterance_context.lower()
    is_past = any(marker in ctx for marker in _PAST_MARKERS)

    if p in {"today", "aaj", "aj", "ivattu", "indru", "eeroju", "ivvala"}:
        return today.isoformat()
    if p in {"yesterday", "ninne", "netru", "ninna", "ninnu"}:
        return (today - timedelta(days=1)).isoformat()
    if p in {"day_before_yesterday", "parso", "parson", "monne", "munnaal"}:
        return (today - timedelta(days=2)).isoformat()
    if p in {"kal", "kal raat", "kal subah", "kal sham"}:
        if not is_past and any(f in ctx for f in ("dega", "denge", "kal aayega", "tomorrow")):
            return (today + timedelta(days=1)).isoformat()
        return (today - timedelta(days=1)).isoformat()
    if p == "tomorrow":
        return (today + timedelta(days=1)).isoformat()

    return today.isoformat()
