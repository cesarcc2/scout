"""Title normalization.

Second-hand ad titles are written on phones, in a hurry, in two languages, with
inconsistent spacing. Almost all of that variance is mechanical and can be
flattened deterministically. What survives this function is a short token
string that simple rules can match reliably — which is why this project needs
so little AI.
"""

from __future__ import annotations

import re
import unicodedata

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
# rtx4070 -> rtx 4070 ; 5070ti -> 5070 ti ; gtx1080ti -> gtx 1080 ti
_ALPHA_DIGIT = re.compile(r"(?<=[a-z])(?=[0-9])")
_DIGIT_ALPHA = re.compile(r"(?<=[0-9])(?=[a-z])")
# "16 gb" / "16go" -> "16gb"  (run AFTER the boundary split, which creates the gap)
_VRAM = re.compile(r"\b(\d{1,2})\s*(?:gb|go|gigas)\b")
# Screen resolutions, dropped entirely. "1080p" survives the letter/digit split
# as the tokens {1080, p}, and 1080 is a GPU model number — so a monitor
# advertised as "1080p 144Hz" matched the GTX 1080 rule and walked straight
# into that card's price distribution. Resolutions are never model numbers, so
# the safe fix is to delete them before matching rather than to teach every
# affected rule about them.
_RESOLUTION = re.compile(r"\b\d{3,4} p\b")
_MULTISPACE = re.compile(r"\s+")

# Marketing filler: never identifies a product, does confuse fuzzy matching.
# Only stripped for the fuzzy fallback, never for rule matching.
_NOISE = {
    "placa", "grafica", "graficas", "gpu", "geforce", "nvidia", "amd",
    "radeon", "intel", "arc", "vga", "usada", "usado", "usadas", "seminova",
    "seminovo", "excelente", "estado", "oportunidade", "envio", "gratis",
    "portes", "incluidos", "urgente", "barata", "barato", "top", "gaming",
    "oc", "edition", "ed", "vendo", "venda", "como", "nova", "novo",
}


def strip_accents(value: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFD", value)
        if unicodedata.category(ch) != "Mn"
    )


def normalize(text: str) -> str:
    """Lowercase, de-accent, split letter/digit runs, unify VRAM, collapse space.

    Note for non-GPU catalogs: the letter/digit split also turns "i7" into
    "i 7". That is fine as long as your match rules key on the distinctive part
    of the model number ("13700k"), which they should anyway.
    """
    if not text:
        return ""
    out = strip_accents(text.lower())
    out = _NON_ALNUM.sub(" ", out)
    out = _ALPHA_DIGIT.sub(" ", out)
    out = _DIGIT_ALPHA.sub(" ", out)
    out = _MULTISPACE.sub(" ", out).strip()
    out = _VRAM.sub(r"\1gb", out)
    out = _RESOLUTION.sub(" ", out)
    return _MULTISPACE.sub(" ", out).strip()


def denoise(normalized_text: str) -> str:
    """Drop marketing filler. Only used for fuzzy alias comparison."""
    return " ".join(t for t in normalized_text.split() if t not in _NOISE)


def tokens_of(normalized_text: str) -> set[str]:
    return set(normalized_text.split())


def has_token(normalized_text: str, tokens: set[str], needle: str) -> bool:
    """Match a catalog token against a normalized title.

    Single words hit the token set — exact, fast, and no accidental substrings
    ("70" must never match inside "5070"). Multi-word catalog entries fall back
    to a word-boundary search on the full string, so catalog authors don't have
    to think about tokenization.
    """
    needle = normalize(needle)
    if not needle:
        return False
    if " " not in needle:
        return needle in tokens
    return re.search(rf"\b{re.escape(needle)}\b", normalized_text) is not None
