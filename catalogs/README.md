# Catalog format

A catalog is one YAML file per product category. Nothing in `src/` knows what a
GPU is — add `catalogs/bikes.yaml` and the whole pipeline works on bikes.

```yaml
category: gpu                # unique id, used as a DB key
label: "Graphics cards"
currency: EUR

# What the collectors search for on each site. Keep these broad; the matcher
# does the precision work. Overly narrow queries mean you miss mistyped ads.
query_terms:
  - "rtx 5070"
  - "radeon 9070"

# Numeric attributes used for cross-product value ranking and filtering.
# `rank_by` is the one the compare view divides price by.
attributes:
  perf_index:
    label: "Relative performance"
    higher_is_better: true
  vram_gb:
    label: "VRAM (GB)"
    higher_is_better: true
rank_by: perf_index

# Regex applied to the normalized title + description. Two jobs:
#  - `exclude: true`      -> throw the listing away entirely
#  - `price_adjust_pct`   -> normalize the price so listings compare like-for-like
#
# The sign convention: a listing WITH warranty is worth more, so to compare it
# against a bare unit we shave value off its asking price. Negative = "this
# listing is better than its price suggests".
modifiers:
  - id: warranty
    patterns: ["garantia", "warranty"]
    price_adjust_pct: -8
  - id: faulty
    patterns: ["para pecas", "avariad"]
    exclude: true

products:
  - id: rtx_5070_ti          # stable key, never change it once data exists
    label: "GeForce RTX 5070 Ti"
    brand: nvidia
    released: 2025-02
    attributes: {perf_index: 100, vram_gb: 16, tdp_w: 300}
    retail_fallback_eur: 879 # only used until the retail scraper has real data
    match:
      all: ["5070", "ti"]    # every token must be present
      any_of: []             # at least one must be present (empty = ignored)
      none_of: ["super"]     # none may be present  <- this is what stops
                             #    "RTX 5070" matching the "5070 Ti" rule
    aliases:                 # fuzzy fallback only, for titles the rules miss
      - "5070ti"
      - "rtx5070 ti"
```

## Matching order

1. Title + description are normalized (lowercase, accents stripped, `5070ti`
   split into `5070 ti`, punctuation collapsed).
2. Exclusion modifiers run first. A "procuro RTX 5070" wanted-ad never reaches
   the matcher.
3. Every product's rule is evaluated. The winner is the one with the most
   required tokens matched — so `4070 ti super` beats `4070 ti` beats `4070`.
4. Anything unmatched goes to fuzzy alias matching at a high threshold, and is
   flagged `fuzzy` so you can audit it.
5. Still unmatched -> written to `unmatched_titles` and exported by
   `scout export-unmatched`. That file is the only thing you ever need to show
   an LLM: paste it into a chat, get back catalog YAML, append, done.

## Attribute values

`perf_index` values shipped in `gpu.yaml` are **approximations and must be
verified before you trust the compare view.** Replace them with TechPowerUp's
relative-performance percentages or your own 3DMark numbers — the ranking is
only as good as this table. They are deliberately plain YAML so this is a
two-minute job, and they change maybe twice a year.
