"""Fuzzy & Indic phonetic customer name resolution for entity disambiguation."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.db.seed import normalize_name


_HONORIFICS = {
    "ji", "bhai", "bhaiya", "bhayya", "sir", "madam", "anna", "akka",
    "amma", "garu", "avare", "avargale", "saheb", "sahab", "seth",
}


@dataclass
class CustomerMatchResult:
    status: str  # "exact" | "single" | "ambiguous" | "none"
    matched: dict | None
    candidates: list[dict]


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def strip_honorifics(name: str) -> str:
    """Remove Indian conversational honorific suffixes while preserving surname/initials."""
    tokens = [t for t in normalize_name(name).split() if t not in _HONORIFICS]
    return " ".join(tokens)


def phonetic_key(token: str) -> str:
    """Normalize common Indic ASR transliteration ambiguities (Laxmi/Lakshmi, Ramesha/Ramesh, etc.)."""
    s = re.sub(r"[^a-z]", "", token.lower())
    if not s:
        return ""
    for src, dst in (
        ("ksh", "ks"),
        ("x", "ks"),
        ("sh", "s"),
        ("bh", "b"),
        ("ph", "f"),
        ("dh", "d"),
        ("th", "t"),
        ("kh", "k"),
        ("gh", "g"),
        ("jh", "j"),
        ("ee", "i"),
        ("oo", "u"),
        ("aa", "a"),
        ("w", "v"),
        ("z", "j"),
        ("q", "k"),
    ):
        s = s.replace(src, dst)
    # Collapse doubled consonants/vowels ("raam" -> "ram", "abduul" -> "abdul")
    s = re.sub(r"(.)\1+", r"\1", s)
    # Strip trailing Kannada/Telugu nominative vowel on longer stems ("ramesha" -> "rames")
    if len(s) >= 5 and s[-1] in ("a", "u"):
        s = s[:-1]
    return s


def _tokens_match(target_tok: str, name_tok: str) -> bool:
    if name_tok == target_tok:
        return True
    if len(target_tok) >= 3 and name_tok.startswith(target_tok):
        return True
    pk_t, pk_n = phonetic_key(target_tok), phonetic_key(name_tok)
    if pk_t and pk_t == pk_n:
        return True
    return _similarity(target_tok, name_tok) >= 0.85


def resolve_customer(source: object, spoken_name: str) -> CustomerMatchResult:
    """Find matching customers using exact, token-prefix, and Indic phonetic similarity."""
    target = strip_honorifics(spoken_name)
    if not target:
        return CustomerMatchResult(status="none", matched=None, candidates=[])

    if isinstance(source, sqlite3.Connection):
        rows = [dict(r) for r in source.execute("SELECT * FROM customers ORDER BY name ASC").fetchall()]
    elif isinstance(source, list):
        rows = list(source)
    elif hasattr(source, "list_customers_raw"):
        rows = source.list_customers_raw()
    else:
        raise TypeError(f"Unsupported customer lookup source: {type(source)!r}")

    if not rows:
        return CustomerMatchResult(status="none", matched=None, candidates=[])

    # 1. Exact normalized match
    for row in rows:
        if row["normalized_name"] == target:
            return CustomerMatchResult(status="exact", matched=row, candidates=[row])

    # 2. Multi-language token & phonetic skeleton match ("Ramesha" -> Ramesh Kumar & Ramesh Gowda)
    target_tokens = target.split()
    token_matches: list[dict] = []
    for row in rows:
        name_tokens = row["normalized_name"].split()
        if all(any(_tokens_match(tt, nt) for nt in name_tokens) for tt in target_tokens):
            token_matches.append(row)

    if len(token_matches) == 1:
        return CustomerMatchResult(status="single", matched=token_matches[0], candidates=token_matches)
    if len(token_matches) > 1:
        return CustomerMatchResult(status="ambiguous", matched=None, candidates=token_matches)

    # 3. Fuzzy whole-name + phonetic similarity fallback ("Sures"/"Rames"/"Laksmi Devi")
    target_pk = "".join(phonetic_key(t) for t in target_tokens)
    scored: list[tuple[float, dict]] = []
    for row in rows:
        row_tokens = row["normalized_name"].split()
        row_pk = "".join(phonetic_key(t) for t in row_tokens)
        score = max(
            _similarity(target, row["normalized_name"]),
            _similarity(target_pk, row_pk) if target_pk and row_pk else 0.0,
            max((_similarity(target, tok) for tok in row_tokens), default=0.0),
        )
        if score >= 0.74:
            scored.append((score, row))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    if not scored:
        return CustomerMatchResult(status="none", matched=None, candidates=[])

    top_score = scored[0][0]
    close_candidates = [row for score, row in scored if top_score - score <= 0.07]
    if len(close_candidates) == 1:
        return CustomerMatchResult(status="single", matched=close_candidates[0], candidates=close_candidates)
    return CustomerMatchResult(status="ambiguous", matched=None, candidates=close_candidates[:5])
