from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from ..config import settings
from .text import normalize


@dataclass(slots=True)
class MatchRule:
    all: list[str] = field(default_factory=list)
    any_of: list[str] = field(default_factory=list)
    none_of: list[str] = field(default_factory=list)

    @property
    def specificity(self) -> int:
        """More required tokens = more specific. This is what makes
        '4070 ti super' win over '4070 ti' over '4070'."""
        return len(self.all) * 10 + len(self.any_of)


@dataclass(slots=True)
class Product:
    id: str
    label: str
    brand: str = ""
    attributes: dict[str, float] = field(default_factory=dict)
    retail_fallback_eur: float = 0.0
    match: MatchRule = field(default_factory=MatchRule)
    aliases: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Modifier:
    id: str
    patterns: list[re.Pattern]
    exclude: bool = False
    price_adjust_pct: float = 0.0
    note: str = ""


@dataclass(slots=True)
class Catalog:
    category: str
    label: str
    currency: str
    query_terms: list[str]
    attributes: dict[str, dict]
    rank_by: str
    modifiers: list[Modifier]
    products: list[Product]
    version: str

    def product(self, product_id: str) -> Product | None:
        return next((p for p in self.products if p.id == product_id), None)

    @property
    def exclusions(self) -> list[Modifier]:
        return [m for m in self.modifiers if m.exclude]

    @property
    def adjusters(self) -> list[Modifier]:
        return [m for m in self.modifiers if not m.exclude]


def _load_file(path: Path) -> Catalog:
    raw_text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw_text)
    version = hashlib.sha256(raw_text.encode()).hexdigest()[:12]

    modifiers = [
        Modifier(
            id=m["id"],
            patterns=[re.compile(p) for p in m.get("patterns", [])],
            exclude=bool(m.get("exclude", False)),
            price_adjust_pct=float(m.get("price_adjust_pct", 0.0)),
            note=m.get("note", ""),
        )
        for m in data.get("modifiers", [])
    ]

    products = []
    for p in data.get("products", []):
        rule = p.get("match", {}) or {}
        products.append(
            Product(
                id=p["id"],
                label=p.get("label", p["id"]),
                brand=p.get("brand", ""),
                attributes={k: float(v) for k, v in (p.get("attributes") or {}).items()},
                retail_fallback_eur=float(p.get("retail_fallback_eur", 0) or 0),
                match=MatchRule(
                    all=[normalize(t) for t in rule.get("all", [])],
                    any_of=[normalize(t) for t in rule.get("any_of", [])],
                    none_of=[normalize(t) for t in rule.get("none_of", [])],
                ),
                aliases=[normalize(a) for a in p.get("aliases", [])],
            )
        )

    return Catalog(
        category=data["category"],
        label=data.get("label", data["category"]),
        currency=data.get("currency", "EUR"),
        query_terms=data.get("query_terms", []),
        attributes=data.get("attributes", {}),
        rank_by=data.get("rank_by", ""),
        modifiers=modifiers,
        products=products,
        version=version,
    )


@lru_cache(maxsize=None)
def load_all(catalog_dir: str | None = None) -> dict[str, Catalog]:
    directory = Path(catalog_dir or settings.catalog_dir)
    catalogs: dict[str, Catalog] = {}
    for path in sorted(directory.glob("*.yaml")):
        cat = _load_file(path)
        catalogs[cat.category] = cat
    return catalogs


def get(category: str) -> Catalog:
    catalogs = load_all()
    if category not in catalogs:
        raise KeyError(f"no catalog for category {category!r} (have: {list(catalogs)})")
    return catalogs[category]
