"""Indic + English spoken number normalizer, colloquial fraction parser, and noise-confusion guard."""

from __future__ import annotations

import re
from dataclasses import dataclass


# Map Unicode Indic numerals (Devanagari, Kannada, Tamil, Telugu, Gujarati, Bengali) to ASCII digits 1:1
_INDIC_DIGIT_MAP = str.maketrans(
    "०१२३४५६७८९"  # Devanagari
    "೦೧೨೩೪೫೬೭೮೯"  # Kannada
    "௦௧௨௩௪௫௬௭௮௯"  # Tamil
    "౦౧౨౩౪౫౬౭౮౯"  # Telugu
    "૦૧૨૩૪૫૬૭૮૯"  # Gujarati
    "০১২৩৪৫৬৭৮৯",  # Bengali
    "0123456789" * 6,
)

# Complete English + Hindi/Hinglish 0-99 + Kannada/Tamil/Telugu/Marathi cardinals & compound hundreds
_UNITS_AND_COMPOUNDS: dict[str, int] = {
    # English
    "zero": 0, "oh": 0,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "ninty": 90,
    # Hindi / Hinglish / Marathi 0-99
    "shunya": 0, "ek": 1, "ik": 1, "do": 2, "don": 2, "teen": 3, "tin": 3, "chaar": 4, "char": 4,
    "paanch": 5, "panch": 5, "paach": 5, "chhe": 6, "che": 6, "chhah": 6, "saha": 6,
    "saat": 7, "sat": 7, "aath": 8, "ath": 8, "nau": 9, "das": 10, "daha": 10,
    "gyarah": 11, "gyara": 11, "barah": 12, "bara": 12, "terah": 13, "tera": 13,
    "chaudah": 14, "chauda": 14, "pandrah": 15, "pandra": 15,
    "solah": 16, "sola": 16, "satrah": 17, "satra": 17,
    "atharah": 18, "athara": 18, "unnees": 19, "unnis": 19, "bees": 20, "bis": 20, "vees": 20,
    "ikkees": 21, "ikkis": 21, "baees": 22, "bais": 22, "teees": 23, "teis": 23,
    "chaubees": 24, "chaubis": 24, "pachchees": 25, "pachis": 25, "pachchis": 25,
    "chhabbees": 26, "chhabis": 26, "sattaees": 27, "satais": 27,
    "atthaees": 28, "athais": 28, "untees": 29, "untis": 29, "tees": 30, "tis": 30,
    "ikattis": 31, "iktis": 31, "battees": 32, "battis": 32, "taintees": 33, "tentis": 33,
    "chauntees": 34, "chautis": 34, "paintees": 35, "pentis": 35,
    "chhattees": 36, "chhatis": 36, "saintees": 37, "sentis": 37,
    "adtees": 38, "adtis": 38, "untaalis": 39, "untalis": 39,
    "chaalis": 40, "chalis": 40, "chalees": 40,
    "iktalis": 41, "byalis": 42, "taintalis": 43, "chavalis": 44, "paintalis": 45,
    "chhiyalis": 46, "saintalis": 47, "adtalis": 48, "unchas": 49,
    "pachaas": 50, "pachas": 50, "pannaas": 50,
    "ikyavan": 51, "bavan": 52, "tirpan": 53, "chauvan": 54, "pachpan": 55,
    "chhappan": 56, "sattavan": 57, "atthavan": 58, "unsath": 59,
    "saath": 60, "sath": 60,
    "iksath": 61, "basath": 62, "tirsath": 63, "chausath": 64, "painsath": 65,
    "chhiyasath": 66, "sadsath": 67, "adsath": 68, "unhattar": 69,
    "sattar": 70, "ikhattar": 71, "bahattar": 72, "tihattar": 73, "chauhattar": 74,
    "pachhattar": 75, "chhihattar": 76, "sathattar": 77, "athattar": 78, "unasi": 79,
    "assi": 80, "asi": 80, "aishi": 80, "ikyasi": 81, "bayasi": 82, "tirasi": 83, "chaurasi": 84,
    "pachasi": 85, "chhiyasi": 86, "sattasi": 87, "atthasi": 88, "navasi": 89,
    "nabbe": 90, "navvad": 90, "ikyanve": 91, "banve": 92, "tiranve": 93, "chauranve": 94,
    "pachanve": 95, "chhiyanve": 96, "sattanve": 97, "atthanve": 98, "ninyanve": 99,
    # Kannada cardinals & compound hundreds (-nooru and genitive -noora forms)
    "ondu": 1, "eradu": 2, "mooru": 3, "muru": 3, "naalku": 4, "nalku": 4,
    "aidu": 5, "aaru": 6, "elu": 7, "entu": 8, "ombattu": 9, "hattu": 10,
    "hannondhu": 11, "hanneradu": 12, "hadimooru": 13, "hadinaalku": 14, "hadinaidu": 15,
    "ippattu": 20, "ippatta": 20, "muvattu": 30, "muvatta": 30, "nalavattu": 40, "nalavatta": 40,
    "aivattu": 50, "aivatta": 50, "aravattu": 60, "eppattu": 70, "embattu": 80, "tombattu": 90,
    "innooru": 200, "innoora": 200,
    "munnooru": 300, "muunnooru": 300, "munnoora": 300, "muunnoora": 300,
    "naalnooru": 400, "nalknooru": 400, "naalnoora": 400, "nalknoora": 400,
    "ainooru": 500, "ainoora": 500,
    "aarnooru": 600, "aarnoora": 600,
    "elnooru": 700, "elnoora": 700,
    "entnooru": 800, "entnoora": 800,
    "ombainooru": 900, "ombainoora": 900,
    # Tamil cardinals & compound hundreds
    "onnu": 1, "rendu": 2, "moonu": 3, "naalu": 4, "anju": 5, "ezhu": 7, "ettu": 8, "ombathu": 9, "pathu": 10,
    "irupathu": 20, "iruvathu": 20, "muppathu": 30, "naalpathu": 40, "narpathu": 40,
    "aimbathu": 50, "arupathu": 60, "ezhupathu": 70, "enbathu": 80, "thonnooru": 90,
    "irunooru": 200, "naanooru": 400,
    # Telugu cardinals
    "okati": 1, "moodu": 3, "nalugu": 4, "edu": 7, "enimidhi": 8, "thommidhi": 9, "padhi": 10,
    "iravai": 20, "muppai": 30, "nalabhai": 40, "yabhai": 50, "aravai": 60, "debbai": 70, "enabhai": 80, "thombai": 90,
}

_SCALE_WORDS: dict[str, int] = {
    "hundred": 100,
    "sau": 100,
    "so": 100,
    "she": 100,
    "shambhar": 100,
    "nooru": 100,
    "noor": 100,
    "vanda": 100,
    "vandalu": 100,
    "thousand": 1000,
    "hazaar": 1000,
    "hazar": 1000,
    "hajar": 1000,
    "saavira": 1000,
    "aayiram": 1000,
    "veyyi": 1000,
    "velu": 1000,
    "lakh": 100000,
    "lac": 100000,
}

_FRACTION_PREFIXES: dict[str, float] = {
    "sava": 0.25,
    "sawa": 0.25,
    "sadhe": 0.5,
    "saade": 0.5,
    "sadhee": 0.5,
    "dedh": 1.5,
    "derh": 1.5,
    "dhai": 2.5,
    "adhaai": 2.5,
    "paune": -0.25,
}

# Pairs frequently confused under shop street noise when STT confidence is low
CONFUSABLE_AMOUNT_PAIRS: tuple[tuple[int, int], ...] = (
    (340, 430),
    (350, 450),
    (240, 420),
    (130, 310),
    (13, 30),
    (14, 40),
    (15, 50),
    (16, 60),
    (17, 70),
    (18, 80),
    (19, 90),
)

# 'so' and 'she' must only continue an existing number run (never start one, avoiding English 'so'/'she')
_SAFE_START_SCALES = set(_SCALE_WORDS) - {"so", "she"}
_NUMBER_TOKENS = (
    set(_UNITS_AND_COMPOUNDS)
    | set(_SCALE_WORDS)
    | set(_FRACTION_PREFIXES)
    | {"and", "aur", "rupaye", "rupees", "rs"}
)


@dataclass
class ExtractedNumber:
    value: float
    raw_span: str
    start: int
    end: int
    source: str  # "digits" | "spoken_words" | "colloquial_pair"


def _parse_fraction_sequence(tokens: list[str]) -> float | None:
    """Parse Indian colloquial fractions like 'sava sau' (125), 'dedh sau' (150), 'sadhe teen sau' (350)."""
    mod = _FRACTION_PREFIXES.get(tokens[0])
    if mod is None:
        return None
    rest = tokens[1:]
    if not rest:
        return None

    # e.g., "dedh sau" (150), "dhai hazaar" (2500)
    if len(rest) == 1 and rest[0] in _SCALE_WORDS:
        scale = _SCALE_WORDS[rest[0]]
        if tokens[0] == "paune":
            return float(0.75 * scale)
        if tokens[0] in ("dedh", "derh", "dhai", "adhaai"):
            return float(mod * scale)
        # "sava sau" -> 125
        return float((1.0 + mod) * scale)

    # e.g., "sadhe teen sau" (350), "sava do sau" (225), "paune do sau" (175)
    if len(rest) == 2 and rest[0] in _UNITS_AND_COMPOUNDS and rest[1] in _SCALE_WORDS:
        base_unit = _UNITS_AND_COMPOUNDS[rest[0]]
        scale = _SCALE_WORDS[rest[1]]
        if base_unit >= 1:
            return float((base_unit + mod) * scale)
    return None


def _parse_word_sequence(tokens: list[str]) -> float | None:
    """Parse a contiguous list of spoken number tokens into a numeric value."""
    meaningful = [t for t in tokens if t not in {"and", "aur", "rupaye", "rupees", "rs"}]
    if not meaningful:
        return None

    # Handle Indian colloquial fractions first ("sava sau", "dedh sau", "dhai sau", "sadhe teen sau")
    if meaningful[0] in _FRACTION_PREFIXES:
        frac = _parse_fraction_sequence(meaningful)
        if frac is not None and frac > 0:
            return frac

    # Handle colloquial English pairs: "three forty" / "three-forty" -> 340, "four thirty" -> 430
    if len(meaningful) == 2:
        a, b = _UNITS_AND_COMPOUNDS.get(meaningful[0]), _UNITS_AND_COMPOUNDS.get(meaningful[1])
        if a is not None and b is not None:
            if 1 <= a <= 9 and 10 <= b <= 99:
                return float(a * 100 + b)
            if a in {20, 30, 40, 50, 60, 70, 80, 90} and 1 <= b <= 9:
                return float(a + b)

    # Handle "three forty five" -> 345, OR "two oh five" -> 205
    if len(meaningful) == 3:
        a = _UNITS_AND_COMPOUNDS.get(meaningful[0])
        b = _UNITS_AND_COMPOUNDS.get(meaningful[1])
        c = _UNITS_AND_COMPOUNDS.get(meaningful[2])
        if a is not None and b is not None and c is not None:
            if 1 <= a <= 9 and b in {20, 30, 40, 50, 60, 70, 80, 90} and 1 <= c <= 9:
                return float(a * 100 + b + c)
            if 1 <= a <= 9 and meaningful[1] in {"oh", "zero"} and 1 <= c <= 9:
                return float(a * 100 + c)

    # Standard Indian/English positional accumulation
    total = 0
    current = 0
    matched_any = False

    for tok in meaningful:
        if tok in _UNITS_AND_COMPOUNDS:
            current += _UNITS_AND_COMPOUNDS[tok]
            matched_any = True
        elif tok in _SCALE_WORDS:
            scale = _SCALE_WORDS[tok]
            matched_any = True
            if scale == 100:
                current = (current or 1) * 100
            else:
                total += (current or 1) * scale
                current = 0
        else:
            return None

    if not matched_any:
        return None
    val = total + current
    return float(val) if val > 0 else None


def normalize_indic_digits(text: str) -> str:
    """Convert Unicode Indic digits (Devanagari/Kannada/Tamil/Telugu/Gujarati/Bengali) to ASCII."""
    return text.translate(_INDIC_DIGIT_MAP)


def extract_numbers(text: str) -> list[ExtractedNumber]:
    """Extract all monetary numbers (digits, 'k'-shorthand, + Indic/English spoken words) in text order."""
    normalized_digits = normalize_indic_digits(text)
    lowered = normalized_digits.lower().replace("₹", " ").replace("$", " ").replace("rs.", " ").replace("rs", " ")
    # Normalize hyphens inside spoken numbers like "three-forty" -> "three forty"
    normalized = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", lowered)

    found: list[ExtractedNumber] = []
    digit_spans: list[tuple[int, int]] = []

    # 1a. Shorthand 'k' amounts like "2k" or "1.5k" (must NOT match "ka")
    for match in re.finditer(r"\b(\d+(?:\.\d{1,2})?)\s*k\b", normalized):
        val = float(match.group(1)) * 1000.0
        if val > 0:
            found.append(
                ExtractedNumber(
                    value=round(val, 2),
                    raw_span=match.group(0),
                    start=match.start(),
                    end=match.end(),
                    source="digits",
                )
            )
            digit_spans.append((match.start(), match.end()))

    # 1b. Digit numbers (supports commas like 2,450 and decimals like 340.50)
    for match in re.finditer(r"\b\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?\b|\b\d+(?:\.\d{1,2})?\b", normalized):
        if any(ds <= match.start() < de for ds, de in digit_spans):
            continue
        raw = match.group(0)
        val = float(raw.replace(",", ""))
        if val > 0:
            found.append(
                ExtractedNumber(
                    value=val,
                    raw_span=raw,
                    start=match.start(),
                    end=match.end(),
                    source="digits",
                )
            )
            digit_spans.append((match.start(), match.end()))

    # 2. Word-based spoken numbers outside digit spans
    token_matches = list(re.finditer(r"[a-z]+", normalized))
    idx = 0
    while idx < len(token_matches):
        m = token_matches[idx]
        if any(ds <= m.start() < de for ds, de in digit_spans):
            idx += 1
            continue
        word = m.group(0)
        if word in _UNITS_AND_COMPOUNDS or word in _SAFE_START_SCALES or word in _FRACTION_PREFIXES:
            run = [m]
            j = idx + 1
            while j < len(token_matches):
                nxt = token_matches[j]
                gap = normalized[run[-1].end() : nxt.start()]
                if gap.strip() != "":
                    break
                nxt_word = nxt.group(0)
                if nxt_word in _NUMBER_TOKENS:
                    run.append(nxt)
                    j += 1
                else:
                    break

            tokens = [r.group(0) for r in run]
            # Trim trailing filler words like "and" / "aur" / "rupaye"
            while tokens and tokens[-1] in {"and", "aur", "rupaye", "rupees", "rs"}:
                tokens.pop()
                run.pop()

            if tokens:
                parsed = _parse_word_sequence(tokens)
                if parsed is not None and parsed > 0:
                    src = (
                        "colloquial_pair"
                        if (len(tokens) == 2 and all(t in _UNITS_AND_COMPOUNDS for t in tokens))
                        else "spoken_words"
                    )
                    found.append(
                        ExtractedNumber(
                            value=parsed,
                            raw_span=" ".join(tokens),
                            start=run[0].start(),
                            end=run[-1].end(),
                            source=src,
                        )
                    )
            idx = max(j, idx + 1)
        else:
            idx += 1

    found.sort(key=lambda item: item.start)
    return found


def get_confusable_counterpart(amount: float) -> float | None:
    """Return a phonetically confusable number under street noise if one exists."""
    rounded = int(round(amount))
    if abs(amount - rounded) > 1e-6:
        return None
    for a, b in CONFUSABLE_AMOUNT_PAIRS:
        if rounded == a:
            return float(b)
        if rounded == b:
            return float(a)
    return None
