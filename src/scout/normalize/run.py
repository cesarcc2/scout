from __future__ import annotations

import csv
import hashlib
import logging
from pathlib import Path

from ..config import settings
from ..db import connect, query
from . import catalog as catalog_mod
from .matcher import adjusted_cents, match

log = logging.getLogger(__name__)

_UPSERT = """
INSERT INTO normalized (listing_id, category, product_id, match_kind, match_score,
                        excluded_by, modifiers, adjust_pct, adjusted_cents,
                        normalized_title, catalog_version, updated_at)
VALUES (%(listing_id)s, %(category)s, %(product_id)s, %(match_kind)s, %(match_score)s,
        %(excluded_by)s, %(modifiers)s, %(adjust_pct)s, %(adjusted_cents)s,
        %(normalized_title)s, %(catalog_version)s, now())
ON CONFLICT (listing_id) DO UPDATE SET
    product_id       = EXCLUDED.product_id,
    match_kind       = EXCLUDED.match_kind,
    match_score      = EXCLUDED.match_score,
    excluded_by      = EXCLUDED.excluded_by,
    modifiers        = EXCLUDED.modifiers,
    adjust_pct       = EXCLUDED.adjust_pct,
    adjusted_cents   = EXCLUDED.adjusted_cents,
    normalized_title = EXCLUDED.normalized_title,
    catalog_version  = EXCLUDED.catalog_version,
    updated_at       = now()
"""


def normalize_category(category: str, force: bool = False) -> dict[str, int]:
    """Re-derive structured data for a category.

    Idempotent and cheap, so it re-runs from scratch whenever the catalog
    version changes — edit a YAML rule and your entire history is reclassified
    on the next cycle. This is the payoff for keeping the matcher deterministic.
    """
    cat = catalog_mod.get(category)
    stats = {"rule": 0, "fuzzy": 0, "none": 0, "excluded": 0}

    where = "l.category = %(cat)s"
    params: dict = {"cat": category, "ver": cat.version}
    if not force:
        where += (
            " AND (n.listing_id IS NULL OR n.catalog_version IS DISTINCT FROM %(ver)s"
            "      OR n.updated_at < l.last_seen)"
        )

    rows = query(
        f"""
        SELECT l.id, l.title, l.description, l.price_cents
        FROM listing l LEFT JOIN normalized n ON n.listing_id = l.id
        WHERE {where}
        """,
        params,
    )

    with connect() as conn:
        for row in rows:
            res = match(cat, row["title"], row["description"] or "")
            stats[res.kind] = stats.get(res.kind, 0) + 1
            conn.execute(
                _UPSERT,
                {
                    "listing_id": row["id"],
                    "category": category,
                    "product_id": res.product_id,
                    "match_kind": res.kind,
                    "match_score": res.score,
                    "excluded_by": res.excluded_by,
                    "modifiers": res.modifiers,
                    "adjust_pct": res.adjust_pct,
                    "adjusted_cents": adjusted_cents(row["price_cents"], res.adjust_pct),
                    "normalized_title": res.normalized_title,
                    "catalog_version": cat.version,
                },
            )

            if res.kind == "none" and res.normalized_title:
                thash = hashlib.sha1(res.normalized_title.encode()).hexdigest()[:16]
                conn.execute(
                    """
                    INSERT INTO unmatched_title (category, title_hash, sample_title)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (category, title_hash) DO UPDATE SET
                        occurrences = unmatched_title.occurrences + 1,
                        last_seen   = now()
                    """,
                    (category, thash, row["title"][:300]),
                )

    log.info("normalized %s: %s", category, stats)
    return stats


def export_unmatched(category: str, limit: int = 200) -> Path:
    """Write the only file you ever need to show a language model.

    Sorted by frequency, so the top of the file is where a five-minute catalog
    edit buys the most coverage. Paste it into a chat with the prompt below,
    get YAML back, append to the catalog, re-run `normalize --force`.
    """
    rows = query(
        """
        SELECT sample_title, occurrences, first_seen, last_seen
        FROM unmatched_title
        WHERE category = %s AND NOT resolved
        ORDER BY occurrences DESC, last_seen DESC
        LIMIT %s
        """,
        (category, limit),
    )

    out_dir = Path(settings.data_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"unmatched_{category}.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["title", "occurrences", "first_seen", "last_seen"])
        for r in rows:
            writer.writerow([r["sample_title"], r["occurrences"], r["first_seen"], r["last_seen"]])

    prompt_path = out_dir / f"unmatched_{category}_prompt.md"
    prompt_path.write_text(PROMPT_TEMPLATE.format(category=category), encoding="utf-8")
    log.info("exported %d unmatched titles to %s", len(rows), path)
    return path


PROMPT_TEMPLATE = """\
# Catalog-extension prompt ({category})

Paste this, then the contents of `unmatched_{category}.csv`, into a Claude chat.
This is the entire AI surface of the system — a batch job you run by hand, maybe
once a month, when coverage starts slipping.

---

You are extending a product catalog used by a price-tracking pipeline. Below is
a CSV of second-hand listing titles (Portuguese and Spanish, informal) that my
deterministic matcher failed to classify.

For each distinct product you can identify, output a YAML entry in exactly this
format, ready to append to my catalog's `products:` list:

```yaml
  - id: <stable_snake_case_id>
    label: "<canonical product name>"
    brand: <brand>
    attributes: {{perf_index: <number>, vram_gb: <number>, tdp_w: <number>}}
    retail_fallback_eur: <number or 0>
    match:
      all: ["<tokens that MUST appear>"]
      none_of: ["<tokens that must NOT appear, to stop this rule stealing
                 more specific variants>"]
    aliases: ["<common shorthand spellings>"]
```

Rules:
- Titles are normalized before matching: lowercased, accents stripped,
  punctuation removed, and `5070ti` split into `5070 ti`. Write tokens in that
  form.
- `none_of` is the important field. If you add a rule for "RTX 5070", it MUST
  exclude "ti" and "super", or it will swallow those variants.
- Do NOT invent `perf_index` values. Leave them as `null` and list which ones I
  need to look up on TechPowerUp.
- If a group of titles is not a product at all (accessories, wanted ads, whole
  PCs), tell me which regex I should add to `modifiers` as an exclusion instead.
- Ignore anything you cannot identify confidently. A wrong rule silently
  corrupts a price distribution; a missing rule just costs coverage.
"""
