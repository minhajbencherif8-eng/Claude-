"""Small text helpers shared by the extractor and the search index."""

from __future__ import annotations

import re
import unicodedata

# A money amount: optional currency before, digits with thousands separators
# (space, dot, comma, apostrophe, narrow no-break space), optional decimals,
# optional currency after.
_CURRENCY = r"(?:€|\$|£|EUR|USD|GBP|CHF|CAD|DA|DZD|MAD|TND|DH|DT)"
MONEY_RE = re.compile(
    rf"(?P<amount>{_CURRENCY}?\s?\d{{1,3}}(?:[   .,']\d{{3}})+(?:[.,]\d{{1,2}})?\s?{_CURRENCY}?"
    rf"|{_CURRENCY}?\s?\d{{3,}}(?:[.,]\d{{1,2}})?\s?{_CURRENCY}?)"
    r"(?:\s?(?:/\s?(?:mois|month|an|year|yr|mo)|per\s+(?:month|year)|par\s+(?:mois|an)))?",
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    """Lowercase, strip accents and collapse whitespace, for matching."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^\w\s]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def parse_amount(raw: str) -> float | None:
    """Turn '3 500,00 €', '$3,500.00', '3.500,50' or '45000' into a float."""
    if not raw:
        return None
    s = re.sub(r"[^\d.,]", "", raw.replace(" ", " ").replace(" ", " "))
    if not s or not any(c.isdigit() for c in s):
        return None
    last_dot, last_comma = s.rfind("."), s.rfind(",")
    sep = max(last_dot, last_comma)
    # A separator followed by exactly 1 or 2 digits is a decimal separator.
    if sep != -1 and 1 <= len(s) - sep - 1 <= 2:
        whole, frac = s[:sep], s[sep + 1:]
    else:
        whole, frac = s, ""
    whole = re.sub(r"[.,]", "", whole)
    if not whole:
        return None
    try:
        return float(f"{whole}.{frac}" if frac else whole)
    except ValueError:
        return None


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


_ARABIC = re.compile(r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufeff]+")


def strip_arabic(text: str) -> str:
    """Drop Arabic script (bilingual forms repeat every French label in Arabic)."""
    return _ARABIC.sub(" ", text)
