from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from .catalog import Catalog, Product
from .text import denoise, has_token, normalize, tokens_of

FUZZY_THRESHOLD = 88


@dataclass(slots=True)
class MatchResult:
    product_id: str | None = None
    kind: str = "none"          # rule | fuzzy | none | excluded
    score: float = 0.0
    excluded_by: str | None = None
    modifiers: list[str] = field(default_factory=list)
    adjust_pct: float = 0.0
    normalized_title: str = ""

    @property
    def usable(self) -> bool:
        return self.product_id is not None and self.kind in ("rule", "fuzzy")


def _rule_matches(product: Product, norm: str, toks: set[str]) -> bool:
    rule = product.match
    if any(not has_token(norm, toks, t) for t in rule.all):
        return False
    if rule.any_of and not any(has_token(norm, toks, t) for t in rule.any_of):
        return False
    if any(has_token(norm, toks, t) for t in rule.none_of):
        return False
    return bool(rule.all or rule.any_of)


def _fuzzy_match(catalog: Catalog, norm: str) -> tuple[str | None, float]:
    """Last resort. Compares against alias strings only, with noise words
    stripped, at a deliberately high threshold — a wrong match here silently
    corrupts a price distribution, which is worse than no match at all."""
    haystack: dict[str, str] = {}
    for product in catalog.products:
        for alias in product.aliases:
            haystack[alias] = product.id
    if not haystack:
        return None, 0.0
    best = process.extractOne(
        denoise(norm), list(haystack), scorer=fuzz.token_set_ratio,
        score_cutoff=FUZZY_THRESHOLD,
    )
    if not best:
        return None, 0.0
    alias, score, _ = best
    return haystack[alias], float(score)


def match(catalog: Catalog, title: str, description: str = "") -> MatchResult:
    norm_title = normalize(title)
    haystack = normalize(f"{title} {description}")
    toks = tokens_of(norm_title)

    result = MatchResult(normalized_title=norm_title)

    # 1. Exclusions first. Wanted-ads and dead cards must never reach the
    #    price distribution, and running these first also saves work.
    for mod in catalog.exclusions:
        target = norm_title if any(p.pattern.startswith("^") for p in mod.patterns) else haystack
        if any(p.search(target) for p in mod.patterns):
            result.kind = "excluded"
            result.excluded_by = mod.id
            return result

    # 2. Rule matching. Most specific winner takes it.
    candidates = [p for p in catalog.products if _rule_matches(p, norm_title, toks)]
    if candidates:
        winner = max(candidates, key=lambda p: p.match.specificity)
        result.product_id = winner.id
        result.kind = "rule"
        result.score = float(winner.match.specificity)
    else:
        # 3. Fuzzy fallback against aliases.
        pid, score = _fuzzy_match(catalog, norm_title)
        if pid:
            result.product_id = pid
            result.kind = "fuzzy"
            result.score = score

    # 4. Price-normalizing modifiers (only meaningful if we matched something).
    if result.usable:
        for mod in catalog.adjusters:
            if any(p.search(haystack) for p in mod.patterns):
                result.modifiers.append(mod.id)
                result.adjust_pct += mod.price_adjust_pct

    return result


def adjusted_cents(price_cents: int | None, adjust_pct: float) -> int | None:
    """Normalize an asking price so listings compare like-for-like.

    A card sold sealed with warranty and an invoice is genuinely worth more
    than a bare used one, so we shave that premium off before comparing it to
    the distribution. Clamped to avoid a pile-up of modifiers producing
    nonsense.
    """
    if price_cents is None:
        return None
    pct = max(-35.0, min(35.0, adjust_pct))
    return int(round(price_cents * (1 + pct / 100.0)))
