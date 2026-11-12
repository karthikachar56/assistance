"""Sub-20ms multilingual deterministic fast-path parser for kirana ledger turns."""

from __future__ import annotations

import re

from app.schemas.models import (
    ExtractedOperation,
    ExtractedQuery,
    LanguageCode,
    VoiceTurnPlan,
)
from app.services.customer_matcher import phonetic_key, strip_honorifics
from app.services.number_parser import extract_numbers, get_confusable_counterpart, normalize_indic_digits


_WORD_RE = re.compile(r"[a-zA-Z\u0900-\u097F\u0C80-\u0CFF]+")


def _compile_cue_matcher(cues: tuple[str, ...]):
    ascii_cues = [re.escape(c) for c in cues if c.isascii()]
    indic_cues = tuple(c for c in cues if not c.isascii())
    ascii_re = re.compile(r"\b(?:" + "|".join(ascii_cues) + r")\b") if ascii_cues else None
    return ascii_re, indic_cues


def _has_cue(low: str, matcher: tuple[re.Pattern[str] | None, tuple[str, ...]]) -> bool:
    ascii_re, indic_cues = matcher
    if ascii_re and ascii_re.search(low):
        return True
    return any(c in low for c in indic_cues)


_CREDIT_CUES = (
    "udhaar", "udhar", "udhari", "baaki", "baki", "credit", "credited", "khata", "khate",
    "likh", "likho", "daalo", "daal", "jod", "jodo", "haaku", "haku",
    "kadan", "appu", "bill", "le gaya", "leke gaya", "kodi", "add", "added", "owes",
    "उधार", "बाकी", "खाते", "लिखो", "जोड़ो",
    "ಉದಾರ್", "ಸಾಲ", "ಬಾಕಿ", "ಹಾಕು", "ಕೊಡು", "ಖಾತೆ", "ಕ್ರೆಡಿಟ್",
)
_PAYMENT_CUES = (
    "diya", "diye", "diya tha", "de diya", "de diye", "paid", "pay", "payment",
    "debit", "debited", "deduct", "minus", "less", "reduce",
    "jama", "wapas", "returned", "received", "cash", "upi", "gpay", "phonepe", "paytm",
    "madidane", "madida", "kottidane", "kottida", "kottaru", "varavu", "icharu",
    "kuduthaan", "kudutha", "bheja", "vasool", "settled", "mila",
    "दिया", "दिए", "जमा", "वापस",
    "ಕೊಟ್ಟರು", "ಕೊಟ್ಟ", "ಕೊಟ್ಟಿದ್ದಾನೆ", "ಮಾಡಿದ್ದಾರೆ", "ಪಾವತಿ", "ಸಂದಾಯ", "ಪೇಮೆಂಟ್", "ಡೆಬಿಟ್",
)
_CORRECTION_PREFIXES = (
    "nahi", "nahin", "nai", "no", "sorry", "galat", "wrong", "change", "correct",
    "actually", "illa", "alla", "maaf", "ulta", "badlo", "update",
    "नहीं", "गलत", "अल्ला", "ಇಲ್ಲ", "ಅಲ್ಲ", "ತಪ್ಪು",
)
_CREDIT_MATCHER = _compile_cue_matcher(_CREDIT_CUES)
_PAYMENT_MATCHER = _compile_cue_matcher(_PAYMENT_CUES)
_CORRECTION_MATCHER = _compile_cue_matcher(_CORRECTION_PREFIXES)
_CONFIRM_YES = {
    "haan", "han", "ha", "yes", "yeah", "yep", "yup", "ok", "okay", "sure",
    "theek hai", "thik hai", "sahi hai", "correct", "confirm", "confirmed",
    "go ahead", "proceed", "kar do", "kardo", "likh do", "likhdo", "do it",
    "houdu", "haudu", "seri", "avunu", "हाँ", "हां", "ठीक", "ಹೌದು", "ಸರಿ",
}
_CONFIRM_NO = {
    "nahi", "nahin", "no", "nope", "cancel", "cancel it", "abort", "stop",
    "mat karo", "rehne do", "chhodo", "dont", "don't", "beda", "vendam", "vaddu",
    "नहीं", "मत करो", "ಬೇಡ", "ಇಲ್ಲ",
}
_PURE_CONFIRM_WORDS = {
    "haan", "han", "ha", "yes", "yeah", "yep", "yup", "ok", "okay", "sure",
    "confirm", "confirmed", "correct", "proceed", "save", "theek", "thik", "hai",
    "sahi", "go", "ahead", "kar", "do", "kardo", "likh", "likhdo", "it",
    "houdu", "haudu", "seri", "avunu", "no", "nope", "nahi", "nahin", "cancel",
    "abort", "stop", "mat", "karo", "rehne", "chhodo", "dont", "don't", "beda",
    "vendam", "vaddu", "sir", "madam", "ji", "please", "pls", "jarvis", "assistant",
    "brother", "bhai", "हाँ", "हां", "ठीक", "ಹೌದು", "ಸರಿ", "नहीं", "ಬೇಡ", "ಇಲ್ಲ",
}
_GREETING_RE = re.compile(
    r"^(?:hi|hello|hey|namaste|namaskar|namaskara|vanakkam|good\s+(?:morning|afternoon|evening)|kaise\s+ho)"
    r"(?:\s+(?:sir|madam|ji|jarvis|assistant))?$",
    re.IGNORECASE,
)

_STOPWORDS_FOR_NAME = {
    "ka", "ki", "ke", "ko", "ne", "se", "ge", "ku", "la", "cha", "chi", "che",
    "avara", "avra", "gariki", "kaun", "bhai", "bhaiya", "bhayya", "ji", "sir", "madam",
    "anna", "akka", "amma", "garu", "avare", "avargale", "saheb", "seth",
    "jarvis", "hey", "hello", "hi", "ok", "okay", "please", "pls", "kindly", "assistant",
    "add", "added", "deduct", "debit", "debited", "credited", "reduce", "minus", "tell", "check", "show", "give",
    "what", "is", "are", "does", "did", "will", "how", "much", "about", "me", "my", "s",
    "to", "from", "for", "of", "in", "on", "the", "a", "an", "into", "account", "owe", "owes",
    "aaj", "kal", "parso", "parson", "today", "yesterday", "tomorrow", "ivattu",
    "ninne", "ninna", "monne", "netru", "indru", "eeroju", "ivvala", "raat", "subah",
    "udhaar", "udhar", "udhari", "baaki", "baki", "credit", "payment", "pay", "paid",
    "diya", "diye", "tha", "thi", "the", "hai", "hain", "rupaye", "rupees", "rupee", "rs", "bucks", "dollars",
    "total", "balance", "history", "batao", "kitna", "gaya", "aaya", "record", "likho",
    "likh", "daalo", "jod", "jodo", "haaku", "haku", "madidane", "madida", "kottidane",
    "kottida", "kottaru", "kadan", "appu", "icharu", "kuduthaan", "kudutha", "varavu", "kodi", "bill",
    "jama", "wapas", "cash", "upi", "gpay", "phonepe", "paytm", "received", "returned",
    "karo", "kar", "karde", "do", "aur", "and", "dena", "lena", "liya", "liye", "kiya", "kiye",
    "bheja", "bhej", "vasool", "settled", "mila", "mile", "chahiye", "maadi", "madide", "madidru",
    "usne", "uska", "uski", "usko", "unhone", "woh", "abhi", "phir", "bhi", "toh",
    "mein", "me", "pe", "par", "khata", "khate", "hisaab", "hisab", "dikhao", "yestu", "kodabeku", "entha",
    "customer", "customers", "detail", "details", "ledger", "statement", "list", "summary", "all", "every",
    "who", "which", "whom", "whose", "kaun", "kisne", "kis", "only", "just", "individual", "name", "names",
    "breakdown", "took", "can", "right", "now", "today's", "todays", "on", "this",
    "sabhi", "sab", "saare", "page", "tab", "screen", "open", "kholo", "dekho", "view", "navigate", "goto",
    "settings", "setting", "log", "logs", "audit", "home", "dashboard",
    "उधार", "बाकी", "दिया", "दिए", "जमा", "रुपये", "कितना", "कुल", "खाता",
    "ಉದಾರ್", "ಸಾಲ", "ಬಾಕಿ", "ಹಾಕು", "ಕೊಡು", "ಕೊಟ್ಟರು", "ಕೊಟ್ಟ", "ಕೊಟ್ಟಿದ್ದಾರೆ", "ಮಾಡಿದ್ದಾರೆ",
    "ಪಾವತಿ", "ಪೇಮೆಂಟ್", "ಕ್ರೆಡಿಟ್", "ಡೆಬಿಟ್", "ಎಷ್ಟು", "ಒಟ್ಟು", "ಖಾತೆ", "ರೂಪಾಯಿ",
    "ಇವತ್ತು", "ಇಂದು", "ನಿನ್ನೆ", "ಮೊನ್ನೆ", "ಯಾರು", "ಯಾರ", "ಯಾರಿಗೆ", "ಹೆಸರು", "ಪಟ್ಟಿ", "ವಿವರ",
    "ಗ್ರಾಹಕ", "ಗ್ರಾಹಕರ", "ಎಲ್ಲಾ", "ತೋರಿಸು", "ಹೇಳು", "ಟ್ರಾನ್ಸಾಕ್ಷನ್", "ಆಯ್ತು", "ಆಗಿದೆ", "ಬಂದಿದೆ",
}


def _match_confirm_yes_no(low: str) -> bool | None:
    """Match polite affirmative/negative confirmation replies (e.g. 'yes sir', 'sure, go ahead', 'no cancel')."""
    cleaned = re.sub(r"[,!?.]+", " ", low).strip()
    cleaned = re.sub(r"\b(?:sir|madam|ji|please|pls|jarvis|brother|bhai)\b", " ", cleaned)
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return None
    if cleaned in _CONFIRM_YES:
        return True
    if cleaned in _CONFIRM_NO:
        return False
    # Multi-word start checks ("yes add it", "sure go ahead", "haan likh do")
    tokens = cleaned.split()
    if tokens[0] in {"yes", "yeah", "yep", "yup", "sure", "haan", "han", "ha", "confirm", "ok", "okay", "houdu", "haudu", "avunu", "seri"}:
        return True
    if tokens[0] in {"no", "nope", "nahi", "nahin", "cancel", "abort", "beda", "vendam", "vaddu"}:
        return False
    return None


def _detect_date_phrase(text: str) -> str:
    low = text.lower()
    if any(tok in low for tok in ("parso", "parson", "monne", "day before yesterday", "ಮೊನ್ನೆ")):
        return "day_before_yesterday"
    if any(tok in low for tok in ("kal", "yesterday", "ninne", "netru", "ninna", "ನಿನ್ನೆ", "कल")):
        return "kal"
    if any(tok in low for tok in ("aaj", "today", "ivattu", "indru", "eeroju", "ivvala", "ಇವತ್ತು", "ಇಂದು", "आज")):
        return "today"
    return "today"


def _match_known_customer_in_clause(clause: str, known_customer_names: list[str] | None) -> str | None:
    if not known_customer_names:
        return None
    low = clause.lower()
    # 1. Longest full-name substring match first (e.g. "Ramesh Gowda" before "Ramesh")
    for known in sorted(known_customer_names, key=len, reverse=True):
        if re.search(rf"\b{re.escape(known.lower())}\b", low):
            return known
    # 2. Check clean first-token match across known roster
    clause_words = [w for w in re.findall(r"[a-z]+", low) if w not in _STOPWORDS_FOR_NAME]
    clause_pks = {phonetic_key(w) for w in clause_words if len(w) >= 3}
    for word, pk in ((w, phonetic_key(w)) for w in clause_words if len(w) >= 3):
        matching_first = [
            known for known in known_customer_names
            if known.lower().split()[0] == word or phonetic_key(known.lower().split()[0]) == pk
        ]
        if len(matching_first) == 1:
            return matching_first[0]
        if len(matching_first) > 1:
            # Duplicate first name spoken alone -> return first name token so LedgerEngine disambiguates cleanly
            return matching_first[0].split()[0]
    _ = clause_pks
    return None


def _match_known_customers_in_query(clause: str, known_customer_names: list[str] | None) -> list[str]:
    if not known_customer_names:
        return []
    low = clause.lower()
    hits: list[tuple[int, str]] = []
    occupied: list[tuple[int, int]] = []
    for known in sorted(known_customer_names, key=len, reverse=True):
        m = re.search(rf"\b{re.escape(known.lower())}\b", low)
        if m:
            if not any(m.start() < e and m.end() > s for s, e in occupied):
                hits.append((m.start(), known))
                occupied.append((m.start(), m.end()))
    hits.sort(key=lambda item: item[0])
    return [name for _, name in hits]


def _extract_customer_name_from_clause(
    clause: str,
    amount_spans: list[tuple[int, int]],
    known_customer_names: list[str] | None = None,
) -> str | None:
    """Extract customer name using known roster alignment, Indic postpositions, or fallback spans."""
    # Prefer postposition-delimited segment before Hindi/Kannada/Tamil/Telugu/Marathi case markers
    m = re.search(
        r"\b([a-zA-Z][a-zA-Z\s]{1,40}?)\s+(?:ka|ki|ke|ne|ko|ge|ku|la|cha|chi|che|avara|avra|gariki)\b",
        clause,
        re.IGNORECASE,
    )
    if m:
        cleaned_seg = strip_honorifics(m.group(1))
        raw_words = [w for w in re.findall(r"[a-zA-Z]+", cleaned_seg) if w.lower() not in _STOPWORDS_FOR_NAME]
        if raw_words:
            candidate = " ".join(w.capitalize() for w in raw_words[-3:])
            # If known roster has an exact phonetic match, snap to canonical spelling
            if known_customer_names:
                c_pk = "".join(phonetic_key(w) for w in raw_words[-3:])
                for known in known_customer_names:
                    k_pk = "".join(phonetic_key(w) for w in known.split())
                    if k_pk == c_pk:
                        return known
            return candidate

    # Try exact known roster match in clause (outside numbers)
    cleaned_chars = list(clause)
    for start, end in amount_spans:
        for i in range(start, min(end, len(cleaned_chars))):
            cleaned_chars[i] = " "
    cleaned = "".join(cleaned_chars)

    known_hit = _match_known_customer_in_clause(cleaned, known_customer_names)
    if known_hit:
        return known_hit

    words = [w for w in _WORD_RE.findall(cleaned) if w.lower() not in _STOPWORDS_FOR_NAME]
    if not words:
        return None
    return " ".join(w.capitalize() if w.isascii() else w for w in words[:2])


def _parse_single_clause(
    clause: str,
    inherited_customer: str | None = None,
    known_customer_names: list[str] | None = None,
) -> tuple[ExtractedOperation | None, str | None]:
    numbers = extract_numbers(clause)
    if not numbers:
        return None, inherited_customer

    amt = numbers[0].value
    low = clause.lower()
    has_credit = _has_cue(low, _CREDIT_MATCHER)
    has_payment = _has_cue(low, _PAYMENT_MATCHER)

    # Ambiguous phrasing check explicitly highlighted in PS2: "Ramesh ko 300 dena hai"
    direction_ambiguous = False
    if re.search(r"\bko\b.*\bdena\s+hai\b", low) and not has_credit and not has_payment:
        direction_ambiguous = True
        tx_type = "credit"
    elif has_payment and not has_credit:
        tx_type = "payment"
    elif has_credit and not has_payment:
        tx_type = "credit"
    elif has_payment and has_credit:
        explicit_credit = any(
            k in low for k in ("udhaar", "udhar", "credit", "credited", "baaki", "baki", "kadan", "appu")
        )
        explicit_payment = any(
            k in low
            for k in (
                "diya", "diye", "paid", "payment", "debit", "debited", "deduct",
                "minus", "reduce", "less", "jama", "upi", "gpay", "phonepe", "paytm", "wapas",
            )
        )
        if explicit_payment and not explicit_credit:
            tx_type = "payment"
        elif explicit_credit and not explicit_payment:
            tx_type = "credit"
        else:
            tx_type = (
                "payment"
                if any(k in low for k in ("diya", "diye", "paid", "jama", "payment", "upi", "wapas", "debit", "debited", "deduct"))
                else "credit"
            )
    else:
        return None, inherited_customer

    customer = (
        _extract_customer_name_from_clause(clause, [(n.start, n.end) for n in numbers], known_customer_names)
        or inherited_customer
    )
    if not customer:
        return None, inherited_customer

    return (
        ExtractedOperation(
            customer_name=customer,
            amount=amt,
            tx_type=tx_type,
            date_phrase=_detect_date_phrase(clause),
            direction_ambiguous=direction_ambiguous,
        ),
        customer,
    )


_LANG_MAP = (
    (re.compile(r"\b(?:hindi|hi-in)\b", re.IGNORECASE), "hi-IN"),
    (re.compile(r"\b(?:kannada|kn-in)\b", re.IGNORECASE), "kn-IN"),
    (re.compile(r"\b(?:tamil|ta-in)\b", re.IGNORECASE), "ta-IN"),
    (re.compile(r"\b(?:telugu|te-in)\b", re.IGNORECASE), "te-IN"),
    (re.compile(r"\b(?:english|en-in)\b", re.IGNORECASE), "en-IN"),
)


def _parse_delete_customer_guard(
    text: str, language: LanguageCode, known_customer_names: list[str] | None = None
) -> VoiceTurnPlan | None:
    """Detect attempts to ask AI to delete/remove a customer and block them safely (manual-only delete)."""
    low = text.lower()
    if "transcript" in low:
        return None
    has_del = any(re.search(rf"\b{v}\b", low) for v in ("delete", "remove", "erase", "hatao", "mitao", "del"))
    if not has_del:
        return None
    has_cust_ref = any(w in low for w in ("customer", "grahak", "account", "client")) or (
        _match_known_customer_in_clause(text, known_customer_names) is not None
    )
    if not has_cust_ref:
        return None
    return VoiceTurnPlan(
        transcript=text,
        intent="delete_customer_blocked",
        confidence=0.99,
        detected_language=language,
    )


def _parse_update_customer(
    text: str, language: LanguageCode, known_customer_names: list[str] | None = None
) -> VoiceTurnPlan | None:
    """Detect explicit AI/voice requests to edit/update an existing customer's profile details."""
    low = text.lower()
    if re.search(
        r"\b(?:udhaar|udhar|credit|credited|debit|debited|payment|paid|pay|baaki|baki|diya|diye|jama|wapas|deduct|minus)\b",
        low,
    ):
        return None

    has_edit_verb = any(
        re.search(rf"\b{v}\b", low)
        for v in ("edit", "update", "change", "modify", "rename", "set", "badlo", "enable", "disable", "turn")
    )
    has_cust_indicator = (
        any(w in low for w in ("customer", "grahak", "phone", "mobile", "language", "opt in", "opt-in", "opt out", "opt-out", "reminder", "rename"))
        or _match_known_customer_in_clause(text, known_customer_names) is not None
    )
    if not (has_edit_verb and has_cust_indicator):
        return None

    phone: str | None = None
    cleaned = text
    phone_m = re.search(r"(?:\+?\d[\d\s\-]{6,14}\d)", text)
    if phone_m:
        digits = re.sub(r"\D", "", phone_m.group(0))
        if len(digits) >= 7:
            phone = phone_m.group(0).strip()
            cleaned = (text[: phone_m.start()] + " " + text[phone_m.end() :]).strip()

    opt_in: bool | None = None
    if re.search(r"\b(?:opt\s*-?\s*out|disable\b.*\breminder|turn\s+off\b.*\breminder|no\s+reminder)\b", low):
        opt_in = False
    elif re.search(r"\b(?:opt\s*-?\s*in|enable\b.*\breminder|turn\s+on\b.*\breminder)\b", low):
        opt_in = True

    pref_lang: LanguageCode | None = None
    for pat, code in _LANG_MAP:
        if pat.search(low):
            pref_lang = code  # type: ignore[assignment]
            break

    # Check rename pattern: "rename Ramesh Gowda to Ramesh Rao" or "change customer Anand Sharma name to Anand Rao"
    target_customer: str | None = None
    new_name: str | None = None
    rename_m = re.search(
        r"(?:\brename\s+(?:customer\s+)?(?P<old>.+?)\s+(?:to|as)\s+(?P<new>.+)$|\bchange\s+(?:customer\s+)?(?P<old2>.+?)\s+name\s+(?:to|as)\s+(?P<new2>.+)$|\bedit\s+(?:customer\s+)?(?P<old3>.+?)\s+name\s+(?:to|as)\s+(?P<new3>.+)$|\bupdate\s+(?:customer\s+)?(?P<old4>.+?)\s+name\s+(?:to|as)\s+(?P<new4>.+)$)",
        cleaned,
        re.IGNORECASE,
    )
    if rename_m:
        raw_old = (rename_m.group("old") or rename_m.group("old2") or rename_m.group("old3") or rename_m.group("old4") or "").strip()
        raw_new = (rename_m.group("new") or rename_m.group("new2") or rename_m.group("new3") or rename_m.group("new4") or "").strip()
        raw_new = re.split(r"\b(?:phone|mobile|language|opt|reminder)\b", raw_new, maxsplit=1, flags=re.IGNORECASE)[0].strip()
        target_customer = _match_known_customer_in_clause(raw_old, known_customer_names) or raw_old.title()
        new_parts = [p for p in re.sub(r"[,:;.\-?!]+", " ", raw_new).split() if p]
        if new_parts:
            new_name = " ".join(p.capitalize() if p.isascii() else p for p in new_parts[:4])

    if not target_customer:
        target_customer = _match_known_customer_in_clause(cleaned, known_customer_names)

    if not target_customer:
        stripped = re.sub(
            r"\b(?:please|pls|kindly|jarvis|sir|ji|hey|hi|hello|edit|update|change|modify|rename|set|badlo|enable|disable|turn|on|off|customer|grahak|details|detail|profile|phone|mobile|number|language|opt|in|out|reminder|reminders|to|for|of|the|a|an|is|as|hindi|kannada|tamil|telugu|english)\b",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
        stripped = re.sub(r"[,:;.\-?!]+", " ", stripped)
        parts = [p for p in stripped.split() if len(p) >= 1]
        if parts:
            target_customer = " ".join(p.capitalize() if p.isascii() else p for p in parts[:4])

    if not target_customer:
        return None

    return VoiceTurnPlan(
        transcript=text,
        intent="update_customer",
        edit_target_customer=target_customer,
        edit_new_name=new_name,
        edit_new_phone=phone,
        edit_opt_in=opt_in,
        edit_preferred_language=pref_lang,
        confidence=0.96,
        detected_language=language,
    )


def _parse_create_customer(text: str, language: LanguageCode) -> VoiceTurnPlan | None:
    """Detect explicit voice/text requests to add/create/register a new customer."""
    low = text.lower()
    # Must explicitly ask to add/create/register a customer and NOT contain ledger credit/debit cues
    has_customer_word = any(w in low for w in ("customer", "grahak", "ग्राहक", "ಗ್ರಾಹಕ"))
    has_create_verb = any(
        re.search(rf"\b{v}\b", low)
        for v in ("add", "create", "register", "new", "naya", "nayi", "banao", "jodo", "jod", "save")
    )
    if not (has_customer_word and has_create_verb):
        return None
    if re.search(r"\b(?:udhaar|udhar|credit|credited|debit|debited|payment|paid|pay|baaki|baki|diya|diye|jama|wapas|deduct|minus|total|details|detail|list|all)\b", low):
        return None

    # Extract optional phone number (7-13 digits, optional +91 / spaces / hyphens)
    phone: str | None = None
    cleaned = text
    phone_m = re.search(r"(?:\+?\d[\d\s\-]{6,14}\d)", text)
    if phone_m:
        digits = re.sub(r"\D", "", phone_m.group(0))
        if len(digits) >= 7:
            phone = phone_m.group(0).strip()
            cleaned = (text[: phone_m.start()] + " " + text[phone_m.end() :]).strip()

    opt_in = any(p in low for p in ("opt in", "opt-in", "optin", "reminder", "reminders"))

    # Remove trigger/filler words to isolate the customer's name
    stripped = re.sub(
        r"\b(?:please|pls|kindly|jarvis|sir|ji|hey|hi|hello|can|you|could|would|want|to|me|my|our|the|a|an|for|with|and|ka|ki|ke|ko|naam|name|named|called|new|naya|nayi|add|create|register|save|make|banao|karo|kardo|jodo|jod|customer|grahak|phone|mobile|number|no|opt|in|optin|reminder|reminders)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    stripped = re.sub(r"[,:;.\-?!]+", " ", stripped)
    name_parts = [p for p in stripped.split() if len(p) >= 1]
    cust_name = " ".join(p.capitalize() if p.isascii() else p for p in name_parts[:4]) if name_parts else None

    return VoiceTurnPlan(
        transcript=text,
        intent="create_customer",
        new_customer_name=cust_name,
        new_customer_phone=phone,
        new_customer_opt_in=opt_in,
        confidence=0.97 if cust_name else 0.90,
        detected_language=language,
    )


def try_fast_path(
    transcript: str,
    language: LanguageCode,
    pending_action: dict | None = None,
    known_customer_names: list[str] | None = None,
) -> VoiceTurnPlan | None:
    """Attempt instant deterministic parsing (<20ms) before falling back to Gemini escalation."""
    text = normalize_indic_digits(" ".join(transcript.strip().split()))
    if not text:
        return None
    low = text.lower().strip(" ?!.")

    if not pending_action and _GREETING_RE.match(low):
        return VoiceTurnPlan(
            transcript=text,
            intent="greeting",
            confidence=0.95,
            detected_language=language,
        )

    if not pending_action and any(
        p in low for p in ("sunai de raha", "sunayi de raha", "can you hear", "hear me", "am i audible")
    ):
        reply_map = {
            "kn-IN": "ಹೌದು ಸರ್, ನಿಮ್ಮ ಧ್ವನಿ ಸ್ಪಷ್ಟವಾಗಿ ಕೇಳಿಸುತ್ತಿದೆ! ದಯವಿಟ್ಟು ಖಾತೆ ವಿವರ ಹೇಳಿ.",
            "en-IN": "Yes sir, I can hear you clearly! Tell me which credit, payment, or balance check you want.",
            "hi-IN": "Haan bilkul sir, aapki awaaz bilkul saaf aa rahi hai! Boliye kya udhaar ya hisaab likhna hai?",
        }
        return VoiceTurnPlan(
            transcript=text,
            intent="greeting",
            confidence=0.96,
            clarification_question=reply_map.get(language, reply_map["hi-IN"]),
            detected_language=language,
        )

    if not pending_action and any(
        p in low for p in ("what can you do", "what all can you do", "explain briefly what you can do", "kya kar sakte")
    ):
        reply_map = {
            "kn-IN": "ಸರ್, ನಾನು ಧ್ವನಿಯ ಮೂಲಕ ಉದಾರ್ ಮತ್ತು ಪೇಮೆಂಟ್ ದಾಖಲಿಸಬಲ್ಲೆ, ಗ್ರಾಹಕರ ಬಾಕಿ ತೋರಿಸಬಲ್ಲೆ ಮತ್ತು ಇಂದಿನ ಒಟ್ಟು ಲೆಕ್ಕ ಹೇಳಬಲ್ಲೆ.",
            "en-IN": "Sir, I can record customer credit (udhaar) and payments by voice, check daily totals, show balances, and add or edit customers.",
            "hi-IN": "Sir, main voice se customer udhaar aur payment likh sakta hoon, aaj ka hisaab aur baaki balance bata sakta hoon, aur customer add/edit kar sakta hoon.",
        }
        return VoiceTurnPlan(
            transcript=text,
            intent="greeting",
            confidence=0.96,
            clarification_question=reply_map.get(language, reply_map["hi-IN"]),
            detected_language=language,
        )

    # 1. Reply to pending disambiguation ("Kaunse Ramesh? Ramesh Kumar ya Ramesh Gowda?")
    if pending_action and pending_action.get("kind") in ("disambiguation", "disambiguation_edit_customer"):
        is_edit_disambig = pending_action.get("kind") == "disambiguation_edit_customer"
        candidates: list[str] = pending_action.get("candidates", [])
        base_low = pending_action.get("base_query", "").lower()
        low_words = re.findall(r"[a-z]+", low)
        low_pks = {phonetic_key(w) for w in low_words}
        for cand in candidates:
            cand_low = cand.lower()
            distinguishing = [tok for tok in cand_low.split() if tok != base_low]
            if cand_low in low or any(
                tok in low_words or phonetic_key(tok) in low_pks
                for tok in distinguishing
            ):
                if is_edit_disambig:
                    return VoiceTurnPlan(
                        transcript=text,
                        intent="update_customer",
                        edit_target_customer=cand,
                        edit_new_name=pending_action.get("new_name"),
                        edit_new_phone=pending_action.get("new_phone"),
                        edit_opt_in=pending_action.get("opt_in"),
                        edit_preferred_language=pending_action.get("preferred_language"),
                        confidence=0.98,
                        detected_language=language,
                    )
                return VoiceTurnPlan(
                    transcript=text,
                    intent="disambiguation_reply",
                    selected_customer=cand,
                    confidence=0.98,
                    detected_language=language,
                )
        if candidates and any(w in low for w in ("first", "pehle", "pahla", "1")):
            return VoiceTurnPlan(
                transcript=text,
                intent="disambiguation_reply",
                selected_customer=candidates[0],
                confidence=0.92,
                detected_language=language,
            )
        if len(candidates) >= 2 and any(w in low for w in ("second", "doosra", "dusra", "2")):
            return VoiceTurnPlan(
                transcript=text,
                intent="disambiguation_reply",
                selected_customer=candidates[1],
                confidence=0.92,
                detected_language=language,
            )

    # 2. Reply to pending confirmation ("haan" / "yes sir" / "sure" / "nahi" / "430")
    if pending_action and pending_action.get("kind") == "confirmation":
        nums = extract_numbers(text)
        if (nums and any(p in low for p in _CORRECTION_PREFIXES)) or (nums and len(low.split()) <= 3):
            return VoiceTurnPlan(
                transcript=text,
                intent="correction",
                corrected_amount=nums[0].value,
                confidence=0.96,
                detected_language=language,
            )
        yn = _match_confirm_yes_no(low)
        if yn is True:
            return VoiceTurnPlan(
                transcript=text,
                intent="confirmation_reply",
                confirmed=True,
                confidence=0.98,
                detected_language=language,
            )
        if yn is False:
            return VoiceTurnPlan(
                transcript=text,
                intent="confirmation_reply",
                confirmed=False,
                confidence=0.98,
                detected_language=language,
            )

    # 2b. Reply to pending "create_customer" prompt asking for customer name
    if pending_action and pending_action.get("kind") == "create_customer":
        yn = _match_confirm_yes_no(low)
        if yn is False:
            return VoiceTurnPlan(
                transcript=text,
                intent="confirmation_reply",
                confirmed=False,
                confidence=0.96,
                detected_language=language,
            )
        cand = _parse_create_customer(f"add customer {text}", language)
        if cand and cand.new_customer_name:
            cand.transcript = text
            return cand

    # 2c. Reply to pending "update_customer" prompt asking what field to edit
    if pending_action and pending_action.get("kind") == "update_customer":
        yn = _match_confirm_yes_no(low)
        if yn is False:
            return VoiceTurnPlan(
                transcript=text,
                intent="confirmation_reply",
                confirmed=False,
                confidence=0.96,
                detected_language=language,
            )
        cname = pending_action.get("customer_name")
        if cname:
            edit_cand = _parse_update_customer(f"edit customer {cname} {text}", language, [cname])
            if edit_cand:
                edit_cand.transcript = text
                return edit_cand

    # 3. Spoken correction without pending confirmation ("nahi, 430", "udhaar nahi payment tha")
    nums = extract_numbers(text)
    has_corr_prefix = _has_cue(low, _CORRECTION_MATCHER)
    if has_corr_prefix:
        # Case 3a: Type reversal correction ("udhaar nahi payment tha" or "galat, 250 payment tha")
        mentions_credit = _has_cue(low, _CREDIT_MATCHER)
        mentions_payment = _has_cue(low, _PAYMENT_MATCHER)
        if ("nahi payment" in low or "nahin payment" in low or "nahi diya" in low or "payment tha" in low or "payment hai" in low) and not _match_known_customer_in_clause(text, known_customer_names):
            return VoiceTurnPlan(
                transcript=text,
                intent="correction",
                corrected_amount=nums[0].value if nums else None,
                corrected_tx_type="payment",
                confidence=0.94,
                detected_language=language,
            )
        if ("payment nahi udhaar" in low or "udhaar tha" in low or "udhar tha" in low or "credit tha" in low) and not _match_known_customer_in_clause(text, known_customer_names):
            return VoiceTurnPlan(
                transcript=text,
                intent="correction",
                corrected_amount=nums[0].value if nums else None,
                corrected_tx_type="credit",
                confidence=0.94,
                detected_language=language,
            )
        # Case 3b: Pure amount correction ("nahi, 430", "sorry teen sau")
        if nums and not mentions_credit and not mentions_payment:
            return VoiceTurnPlan(
                transcript=text,
                intent="correction",
                corrected_amount=nums[0].value,
                confidence=0.95,
                detected_language=language,
            )

    # 3b. Manual-only customer delete guard (must happen before Gemini or fallback)
    del_guard = _parse_delete_customer_guard(text, language, known_customer_names)
    if del_guard is not None:
        return del_guard

    # 3c. Explicit customer creation ("Add customer Anand Sharma", "Create customer Priya phone 9876543210")
    create_plan = _parse_create_customer(text, language)
    if create_plan is not None:
        return create_plan

    # 3d. Explicit customer edit/update ("Edit customer Anand Sharma phone 9988776655", "Rename Ramesh Gowda to ...")
    update_plan = _parse_update_customer(text, language, known_customer_names)
    if update_plan is not None:
        return update_plan

    # 4. Fast-path deterministic queries (<15ms!)
    clean_words = re.sub(r"[,!?.]+", " ", low).split()
    yn = _match_confirm_yes_no(low) if (not nums and clean_words and all(w in _PURE_CONFIRM_WORDS for w in clean_words)) else None
    if yn is not None:
        return VoiceTurnPlan(
            transcript=text,
            intent="confirmation_reply",
            confirmed=yn,
            confidence=0.92,
            detected_language=language,
        )

    is_question = "?" in text or any(
        q in low
        for q in (
            "kitna", "total", "balance", "baaki", "hisab", "hisaab", "batao",
            "history", "dikhao", "details", "detail", "ledger", "statement", "open", "kholo", "dekho",
            "show", "tell", "list", "who", "which", "kaun", "kisne", "kis-kis", "kis kis",
            "names", "name", "individual", "breakdown", "only today", "today only",
            "sabse zyada", "sabse jyada", "highest", "who owes",
            "how much", "yestu", "kodabeku", "evaga", "entha", "collection",
            "कितना", "कुल", "ಬಾಕಿ", "ಎಷ್ಟು", "ಒಟ್ಟು",
            "ಯಾರು", "ಯಾರ", "ಯಾರಿಗೆ", "ಇವತ್ತು", "ಇಂದು", "ನಿನ್ನೆ", "ತೋರಿಸು", "ಹೇಳು", "ವಿವರ", "ಪಟ್ಟಿ", "ಟ್ರಾನ್ಸಾಕ್ಷನ್",
        )
    )
    if is_question and not nums:
        date_phrase = _detect_date_phrase(text)
        matched_cust_in_query = _match_known_customer_in_clause(text, known_customer_names)
        asks_date_credit = (
            any(p in low for p in (
                "udhaar gaya", "udhar gaya", "credit today", "today credit", "today's credit",
                "aaj ka udhaar", "kal ka udhaar", "kitna udhaar", "yestu credit", "yestu udhaar",
                "who took credit", "took credit", "credit customer", "customers with credit",
                "kisne udhaar", "kis-kis ka udhaar", "kis kis ka udhaar",
            ))
            or (
                not matched_cust_in_query
                and any(k in low for k in ("credit", "udhaar", "udhar", "ಉದಾರ್", "ಸಾಲ", "ಕ್ರೆಡಿಟ್"))
                and any(w in low for w in (
                    "today", "aaj", "kal", "yesterday", "parso", "gaya", "took", "who", "which",
                    "kisne", "individual", "name", "list",
                    "ಇವತ್ತು", "ಇಂದು", "ನಿನ್ನೆ", "ಯಾರು", "ಯಾರ", "ಎಷ್ಟು",
                ))
            )
            or (
                not matched_cust_in_query
                and any(w in low for w in ("only today", "today only"))
                and not any(p in low for p in ("payment", "paid", "jama", "vasool", "collection", "ಪೇಮೆಂಟ್", "ಪಾವತಿ"))
            )
        )
        asks_date_payment = (
            any(p in low for p in (
                "payment aaya", "kitna payment", "kitna aaya", "collection", "payments today",
                "today payment", "today's payment", "jama hua", "vasool",
                "who paid", "payment customer", "customers who paid", "kisne payment", "kisne diya",
            ))
            or (
                not matched_cust_in_query
                and any(k in low for k in ("payment", "payments", "paid", "jama", "vasool", "ಪೇಮೆಂಟ್", "ಪಾವತಿ", "ಸಂದಾಯ"))
                and any(w in low for w in (
                    "today", "aaj", "kal", "yesterday", "parso", "who", "which", "kisne", "individual",
                    "name", "list", "only today", "today only",
                    "ಇವತ್ತು", "ಇಂದು", "ನಿನ್ನೆ", "ಯಾರು", "ಯಾರ", "ಮಾಡಿದ್ದಾರೆ", "ಕೊಟ್ಟಿದ್ದಾರೆ", "ಬಂದಿದೆ", "ಎಷ್ಟು",
                ))
            )
        )
        if asks_date_credit:
            return VoiceTurnPlan(
                transcript=text,
                intent="query_ledger",
                query=ExtractedQuery(kind="credit_sum_for_date", date_phrase=date_phrase),
                confidence=0.96,
                detected_language=language,
            )
        if asks_date_payment:
            return VoiceTurnPlan(
                transcript=text,
                intent="query_ledger",
                query=ExtractedQuery(kind="payment_sum_for_date", date_phrase=date_phrase),
                confidence=0.96,
                detected_language=language,
            )
        # Explicit all-customers / total customer details request
        if any(
            p in low
            for p in (
                "total customer", "all customer", "sabhi customer", "saare customer", "sab customer",
                "ಎಲ್ಲಾ ಗ್ರಾಹಕ", "ಒಟ್ಟು ಗ್ರಾಹಕ", "ಗ್ರಾಹಕರ ವಿವರ", "ಗ್ರಾಹಕರ ಪಟ್ಟಿ",
            )
        ) or (
            not matched_cust_in_query
            and any(
                p in low
                for p in (
                    "customer detail", "customers detail", "customers list",
                    "list customer", "show customer", "customer summary", "customer names", "individual customer",
                )
            )
        ):
            return VoiceTurnPlan(
                transcript=text,
                intent="query_ledger",
                query=ExtractedQuery(kind="all_customers"),
                confidence=0.97,
                detected_language=language,
            )
        # Explicit page/tab navigation commands
        if any(p in low for p in ("open settings", "show settings", "go to settings", "settings page", "settings kholo")):
            return VoiceTurnPlan(
                transcript=text,
                intent="navigate_page",
                target_page="settings",
                confidence=0.97,
                detected_language=language,
            )
        if any(p in low for p in ("open customers", "customers page", "customers tab", "go to customers", "customer page")):
            return VoiceTurnPlan(
                transcript=text,
                intent="navigate_page",
                target_page="customers",
                confidence=0.97,
                detected_language=language,
            )
        if any(p in low for p in ("open khata", "open dashboard", "go home", "home page", "dashboard page", "main page")):
            return VoiceTurnPlan(
                transcript=text,
                intent="navigate_page",
                target_page="home",
                confidence=0.97,
                detected_language=language,
            )
        if any(p in low for p in ("sabse zyada", "sabse jyada", "highest", "most", "top", "ಅತಿ ಹೆಚ್ಚು")):
            return VoiceTurnPlan(
                transcript=text,
                intent="query_ledger",
                query=ExtractedQuery(kind="top_debtor"),
                confidence=0.96,
                detected_language=language,
            )
        if any(p in low for p in (
            "total outstanding", "total baaki", "kul baaki", "sabka total", "dukaan ka total", "dukaan ka kul",
            "who owes", "outstanding customer", "pending customer", "ಒಟ್ಟು ಬಾಕಿ", "ಅಂಗಡಿಯ ಬಾಕಿ",
        )):
            return VoiceTurnPlan(
                transcript=text,
                intent="query_ledger",
                query=ExtractedQuery(kind="total_outstanding"),
                confidence=0.96,
                detected_language=language,
            )

        multi_names = _match_known_customers_in_query(text, known_customer_names)
        if len(multi_names) >= 2:
            kind = (
                "customer_history"
                if any(h in low for h in ("history", "entries", "last", "recent", "detail", "details", "ledger", "statement", "transactions", "pichhle"))
                else "customer_balance"
            )
            return VoiceTurnPlan(
                transcript=text,
                intent="query_ledger",
                query=ExtractedQuery(kind=kind, customer_name=", ".join(multi_names), date_phrase=date_phrase),
                confidence=0.94,
                detected_language=language,
            )

        cust_name = _match_known_customer_in_clause(text, known_customer_names)
        if not cust_name:
            cust_name = _extract_customer_name_from_clause(text, [], known_customer_names)

        if cust_name:
            kind = (
                "customer_history"
                if any(h in low for h in ("history", "entries", "last", "recent", "detail", "details", "ledger", "statement"))
                else "customer_balance"
            )
            return VoiceTurnPlan(
                transcript=text,
                intent="query_ledger",
                query=ExtractedQuery(kind=kind, customer_name=cust_name, date_phrase=date_phrase),
                confidence=0.94,
                detected_language=language,
            )

    # 5. Clean single or multi-clause comma/aur-separated transaction clauses
    clauses = [c.strip() for c in re.split(r",|\baur\b|\band\b|\bmattu\b", text, flags=re.IGNORECASE) if c.strip()]
    operations: list[ExtractedOperation] = []
    inherited_cust: str | None = None
    all_parsed = True
    for clause in clauses:
        op, inherited_cust = _parse_single_clause(clause, inherited_cust, known_customer_names)
        if op is None:
            all_parsed = False
            break
        operations.append(op)

    if all_parsed and operations:
        return VoiceTurnPlan(
            transcript=text,
            intent="record_transactions",
            operations=operations,
            confidence=0.94 if not any(o.direction_ambiguous for o in operations) else 0.72,
            detected_language=language,
        )

    return None


def verify_and_guard_plan(plan: VoiceTurnPlan) -> VoiceTurnPlan:
    """Cross-validate LLM output amounts against deterministic extracted numbers in the transcript."""
    if not plan.operations:
        return plan
    transcript_nums = [n.value for n in extract_numbers(plan.transcript)]
    for op in plan.operations:
        if plan.confidence < 0.75 and not op.ambiguous_amounts:
            counterpart = get_confusable_counterpart(op.amount)
            if counterpart is not None and op.amount not in transcript_nums:
                op.ambiguous_amounts = [op.amount, counterpart]
    return plan
