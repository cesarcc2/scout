"""Generate catalogs/gpu.yaml from sourced data.

Kept in the repo so the numbers are reproducible and their provenance is
auditable — not because the YAML needs generating regularly. Run it, diff the
result, commit both.

perf_index is rescaled from Tom's Hardware's 1440p-ultra rasterisation
hierarchy, where the RTX 5090 = 100%. We re-anchor on the RTX 5070 Ti = 100 so
the number reads as "percent of a 5070 Ti", which is the card most people in
this market are cross-shopping.
"""

from __future__ import annotations

# Tom's Hardware GPU benchmarks hierarchy, 1440p ultra rasterisation, as a
# percentage of the RTX 5090. Fetched 2026-08-15.
# https://www.tomshardware.com/reviews/gpu-hierarchy,4388.html
TH_1440P = {
    "rtx_5090": 100.0, "rtx_5080": 76.7, "rtx_5070_ti": 69.8, "rtx_5070": 57.6,
    "rtx_5060_ti_16": 43.9, "rtx_5060_ti_8": 41.0, "rtx_5060": 35.8,
    "rtx_5050": 27.1,
    "rtx_4090": 85.7, "rtx_4080_super": 70.9, "rtx_4080": 70.3,
    "rtx_4070_ti_super": 62.1, "rtx_4070_ti": 58.6, "rtx_4070_super": 54.5,
    "rtx_4070": 46.5, "rtx_4060_ti_16": 36.2, "rtx_4060_ti_8": 35.2,
    "rtx_4060": 28.4,
    "rtx_3090_ti": 59.7, "rtx_3090": 54.7, "rtx_3080_ti": 53.3,
    "rtx_3080_10": 49.0, "rtx_3070_ti": 40.0, "rtx_3070": 34.8,
    "rtx_3060_ti": 30.5, "rtx_3060_12": 25.0, "rtx_3050": 17.8,
    "rx_9070_xt": 69.7, "rx_9070": 62.1, "rx_9070_gre": 51.8,
    "rx_9060_xt_16": 40.2, "rx_9060_xt_8": 37.3,
    "rx_7900_xtx": 73.1, "rx_7900_xt": 64.6, "rx_7800_xt": 50.7,
    "rx_7700_xt": 43.4, "rx_7600_xt": 30.0, "rx_7600": 27.2,
    "rx_6950_xt": 53.5, "rx_6900_xt": 50.2, "rx_6800_xt": 47.6,
    "rx_6750_xt": 34.4, "rx_6700_xt": 32.5, "rx_6600_xt": 24.3,
    "rx_6650_xt": 22.7, "rx_6600": 14.9,
    "arc_b580": 30.3, "arc_b570": 26.5,
}
ANCHOR = TH_1440P["rtx_5070_ti"]

# Portuguese retail is VAT-inclusive (23%) plus margin. One observed data point
# — the RTX 5070 landed at €669.90 against a $549 MSRP — puts the multiplier at
# about 1.22. Applied only to cards still sold new; everything else gets 0 and
# waits for the retail scraper.
# https://www.geekinout.pt/artigos/rtx-5070-ja-chegou-a-portugal-com-precos-desde-66990eur
PT_FACTOR = 1.22
MSRP_USD = {
    "rtx_5090": 1999, "rtx_5080": 999, "rtx_5070_ti": 749, "rtx_5070": 549,
    "rtx_5060_ti_16": 429, "rtx_5060_ti_8": 379, "rtx_5060": 299,
    "rtx_5050": 249,
    "rx_9070_xt": 599, "rx_9070": 549, "rx_9060_xt_16": 349, "rx_9060_xt_8": 299,
    "arc_b580": 249, "arc_b570": 219,
}


def perf(pid: str) -> float | None:
    raw = TH_1440P.get(pid)
    return round(raw / ANCHOR * 100, 1) if raw else None


def retail(pid: str) -> int:
    usd = MSRP_USD.get(pid)
    return int(round(usd * PT_FACTOR / 10) * 10) if usd else 0


# (id, label, brand, vram_gb, tdp_w, match-all, any_of, none_of, aliases)
# VRAM and TDP are manufacturer specifications, cross-checked against Wikipedia
# — Tom's Hardware's table had the 5090 at 24GB and the 5060 at 12GB, both wrong.
P = [
    # ---- NVIDIA Blackwell (RTX 50) ----------------------------------------
    ("rtx_5090", "GeForce RTX 5090", "nvidia", 32, 575, ["5090"], [], [], ["rtx5090"]),
    ("rtx_5080", "GeForce RTX 5080", "nvidia", 16, 360, ["5080"], [], ["super", "ti"], ["rtx5080"]),
    ("rtx_5070_ti", "GeForce RTX 5070 Ti", "nvidia", 16, 300, ["5070", "ti"], [], ["super"], ["5070ti", "rtx5070ti"]),
    ("rtx_5070", "GeForce RTX 5070", "nvidia", 12, 250, ["5070"], [], ["ti", "super"], ["rtx5070"]),
    ("rtx_5060_ti_16", "GeForce RTX 5060 Ti 16GB", "nvidia", 16, 180, ["5060", "ti"], ["16gb"], [], ["5060ti 16gb"]),
    ("rtx_5060_ti_8", "GeForce RTX 5060 Ti 8GB", "nvidia", 8, 180, ["5060", "ti"], [], ["16gb"], ["5060ti"]),
    ("rtx_5060", "GeForce RTX 5060", "nvidia", 8, 145, ["5060"], [], ["ti"], ["rtx5060"]),
    ("rtx_5050", "GeForce RTX 5050", "nvidia", 8, 130, ["5050"], [], [], ["rtx5050"]),

    # ---- NVIDIA Ada (RTX 40) ----------------------------------------------
    ("rtx_4090", "GeForce RTX 4090", "nvidia", 24, 450, ["4090"], [], [], ["rtx4090"]),
    ("rtx_4080_super", "GeForce RTX 4080 Super", "nvidia", 16, 320, ["4080", "super"], [], [], ["4080s"]),
    ("rtx_4080", "GeForce RTX 4080", "nvidia", 16, 320, ["4080"], [], ["super"], ["rtx4080"]),
    ("rtx_4070_ti_super", "GeForce RTX 4070 Ti Super", "nvidia", 16, 285, ["4070", "ti", "super"], [], [], ["4070tis"]),
    ("rtx_4070_ti", "GeForce RTX 4070 Ti", "nvidia", 12, 285, ["4070", "ti"], [], ["super"], ["4070ti"]),
    ("rtx_4070_super", "GeForce RTX 4070 Super", "nvidia", 12, 220, ["4070", "super"], [], ["ti"], ["4070s"]),
    ("rtx_4070", "GeForce RTX 4070", "nvidia", 12, 200, ["4070"], [], ["ti", "super"], ["rtx4070"]),
    ("rtx_4060_ti_16", "GeForce RTX 4060 Ti 16GB", "nvidia", 16, 165, ["4060", "ti"], ["16gb"], [], ["4060ti 16gb"]),
    ("rtx_4060_ti_8", "GeForce RTX 4060 Ti 8GB", "nvidia", 8, 160, ["4060", "ti"], [], ["16gb"], ["4060ti"]),
    ("rtx_4060", "GeForce RTX 4060", "nvidia", 8, 115, ["4060"], [], ["ti"], ["rtx4060"]),

    # ---- NVIDIA Ampere (RTX 30) — no longer sold new, retail stays 0 ------
    ("rtx_3090_ti", "GeForce RTX 3090 Ti", "nvidia", 24, 450, ["3090", "ti"], [], [], ["3090ti"]),
    ("rtx_3090", "GeForce RTX 3090", "nvidia", 24, 350, ["3090"], [], ["ti"], ["rtx3090"]),
    ("rtx_3080_ti", "GeForce RTX 3080 Ti", "nvidia", 12, 350, ["3080", "ti"], [], [], ["3080ti"]),
    ("rtx_3080_10", "GeForce RTX 3080 10GB", "nvidia", 10, 320, ["3080"], [], ["ti"], ["rtx3080"]),
    ("rtx_3070_ti", "GeForce RTX 3070 Ti", "nvidia", 8, 290, ["3070", "ti"], [], [], ["3070ti"]),
    ("rtx_3070", "GeForce RTX 3070", "nvidia", 8, 220, ["3070"], [], ["ti"], ["rtx3070"]),
    ("rtx_3060_ti", "GeForce RTX 3060 Ti", "nvidia", 8, 200, ["3060", "ti"], [], [], ["3060ti"]),
    ("rtx_3060_12", "GeForce RTX 3060 12GB", "nvidia", 12, 170, ["3060"], [], ["ti"], ["rtx3060"]),
    ("rtx_3050", "GeForce RTX 3050", "nvidia", 8, 130, ["3050"], [], [], ["rtx3050"]),

    # ---- AMD RDNA4 (RX 9000) ----------------------------------------------
    ("rx_9070_xt", "Radeon RX 9070 XT", "amd", 16, 304, ["9070", "xt"], [], ["gre"], ["9070xt", "rx9070xt"]),
    ("rx_9070_gre", "Radeon RX 9070 GRE", "amd", 12, 220, ["9070", "gre"], [], [], ["9070gre"]),
    ("rx_9070", "Radeon RX 9070", "amd", 16, 220, ["9070"], [], ["xt", "gre"], ["rx9070"]),
    ("rx_9060_xt_16", "Radeon RX 9060 XT 16GB", "amd", 16, 160, ["9060", "xt"], ["16gb"], [], ["9060xt 16gb"]),
    ("rx_9060_xt_8", "Radeon RX 9060 XT 8GB", "amd", 8, 150, ["9060", "xt"], [], ["16gb"], ["9060xt"]),

    # ---- AMD RDNA3 (RX 7000) ----------------------------------------------
    ("rx_7900_xtx", "Radeon RX 7900 XTX", "amd", 24, 355, ["7900", "xtx"], [], [], ["7900xtx"]),
    ("rx_7900_xt", "Radeon RX 7900 XT", "amd", 20, 315, ["7900", "xt"], [], ["xtx", "gre"], ["7900xt"]),
    ("rx_7900_gre", "Radeon RX 7900 GRE", "amd", 16, 260, ["7900", "gre"], [], [], ["7900gre"]),
    ("rx_7800_xt", "Radeon RX 7800 XT", "amd", 16, 263, ["7800", "xt"], [], [], ["7800xt"]),
    ("rx_7700_xt", "Radeon RX 7700 XT", "amd", 12, 245, ["7700", "xt"], [], [], ["7700xt"]),
    ("rx_7600_xt", "Radeon RX 7600 XT", "amd", 16, 190, ["7600", "xt"], [], [], ["7600xt"]),
    ("rx_7600", "Radeon RX 7600", "amd", 8, 165, ["7600"], [], ["xt"], ["rx7600"]),

    # ---- AMD RDNA2 (RX 6000) ----------------------------------------------
    ("rx_6950_xt", "Radeon RX 6950 XT", "amd", 16, 335, ["6950", "xt"], [], [], ["6950xt"]),
    ("rx_6900_xt", "Radeon RX 6900 XT", "amd", 16, 300, ["6900", "xt"], [], [], ["6900xt"]),
    ("rx_6800_xt", "Radeon RX 6800 XT", "amd", 16, 300, ["6800", "xt"], [], [], ["6800xt"]),
    ("rx_6800", "Radeon RX 6800", "amd", 16, 250, ["6800"], [], ["xt"], ["rx6800"]),
    ("rx_6750_xt", "Radeon RX 6750 XT", "amd", 12, 250, ["6750", "xt"], [], [], ["6750xt"]),
    ("rx_6700_xt", "Radeon RX 6700 XT", "amd", 12, 230, ["6700", "xt"], [], [], ["6700xt"]),
    ("rx_6650_xt", "Radeon RX 6650 XT", "amd", 8, 180, ["6650", "xt"], [], [], ["6650xt"]),
    ("rx_6600_xt", "Radeon RX 6600 XT", "amd", 8, 160, ["6600", "xt"], [], [], ["6600xt"]),
    ("rx_6600", "Radeon RX 6600", "amd", 8, 132, ["6600"], [], ["xt"], ["rx6600"]),

    # ---- Intel Arc --------------------------------------------------------
    ("arc_b580", "Arc B580", "intel", 12, 190, ["b580"], [], [], ["arc b580"]),
    ("arc_b570", "Arc B570", "intel", 10, 150, ["b570"], [], [], ["arc b570"]),

    # ---- Older cards: tracked for price, absent from the ranking ----------
    # These fill a large share of the Portuguese second-hand market but are not
    # in the sourced performance table. They get no perf_index rather than an
    # invented one, so they are priced and alerted on but sit out the value
    # ranking. Delete any you do not care about — each one costs a search.
    ("rtx_2080_ti", "GeForce RTX 2080 Ti", "nvidia", 11, 250, ["2080", "ti"], [], [], ["2080ti"]),
    ("rtx_2080_super", "GeForce RTX 2080 Super", "nvidia", 8, 250, ["2080", "super"], [], [], ["2080s"]),
    ("rtx_2080", "GeForce RTX 2080", "nvidia", 8, 215, ["2080"], [], ["ti", "super"], ["rtx2080"]),
    ("rtx_2070_super", "GeForce RTX 2070 Super", "nvidia", 8, 215, ["2070", "super"], [], [], ["2070s"]),
    ("rtx_2070", "GeForce RTX 2070", "nvidia", 8, 175, ["2070"], [], ["super"], ["rtx2070"]),
    ("rtx_2060_super", "GeForce RTX 2060 Super", "nvidia", 8, 175, ["2060", "super"], [], [], ["2060s"]),
    ("rtx_2060", "GeForce RTX 2060", "nvidia", 6, 160, ["2060"], [], ["super"], ["rtx2060"]),
    ("gtx_1080_ti", "GeForce GTX 1080 Ti", "nvidia", 11, 250, ["1080", "ti"], [], [], ["1080ti"]),
    ("gtx_1080", "GeForce GTX 1080", "nvidia", 8, 180, ["1080"], [], ["ti"], ["gtx1080"]),
    ("gtx_1070_ti", "GeForce GTX 1070 Ti", "nvidia", 8, 180, ["1070", "ti"], [], [], ["1070ti"]),
    ("gtx_1070", "GeForce GTX 1070", "nvidia", 8, 150, ["1070"], [], ["ti"], ["gtx1070"]),
    ("gtx_1660_super", "GeForce GTX 1660 Super", "nvidia", 6, 125, ["1660", "super"], [], [], ["1660s"]),
    ("gtx_1660_ti", "GeForce GTX 1660 Ti", "nvidia", 6, 120, ["1660", "ti"], [], [], ["1660ti"]),
    ("gtx_1660", "GeForce GTX 1660", "nvidia", 6, 120, ["1660"], [], ["ti", "super"], ["gtx1660"]),
    ("gtx_1650", "GeForce GTX 1650", "nvidia", 4, 75, ["1650"], [], ["super"], ["gtx1650"]),
    ("gtx_1060_6", "GeForce GTX 1060 6GB", "nvidia", 6, 120, ["1060"], [], [], ["gtx1060"]),
]

# Laptop parts share every model number with desktop cards and are worth a
# fraction of the price, so they must never enter a desktop distribution.
# Applied uniformly to every product: match rules read the title only, which is
# exactly the right scope — a laptop card says so in its title.
LAPTOP = ["laptop", "portatil", "notebook"]

HEADER = '''\
category: gpu
label: "Graphics cards"
currency: EUR

# ===========================================================================
# DATA PROVENANCE — read this before trusting the compare view
#
#   perf_index  Tom's Hardware GPU benchmarks hierarchy, 1440p ultra
#               rasterisation, fetched 2026-08-15, rescaled so RTX 5070 Ti =
#               100. Source: https://www.tomshardware.com/reviews/gpu-hierarchy,4388.html
#               Products with no perf_index are deliberately absent from that
#               table; they are still tracked and alerted on, they just sit out
#               the value ranking. Never invent a number to fill one in — a
#               plausible-but-wrong benchmark is worse than none.
#
#   vram_gb     Manufacturer specification. Cross-checked against Wikipedia,
#   tdp_w       which caught two errors in the hierarchy table (RTX 5090 listed
#               as 24GB, RTX 5060 as 12GB).
#
#   retail_*    Estimate only: USD MSRP x 1.22, which is what Portuguese VAT
#               plus margin worked out to on the one observed data point (RTX
#               5070, $549 MSRP, listed at EUR 669.90 in PT). Zero for anything
#               no longer sold new. The retail scraper overrides all of these
#               as soon as it has real prices — these exist so "vs retail" is
#               not blank on day one.
#
# Regenerate with build_gpu_catalog.py; do not hand-edit the numbers without
# updating the source note.
# ===========================================================================

# Broad on purpose. The matcher does precision; the query only has to surface
# the ad. Every term costs a round of requests on every cycle, so widen rather
# than lengthen this list.
query_terms:
  - "rtx 5090"
  - "rtx 5080"
  - "rtx 5070"
  - "rtx 5060"
  - "rtx 4090"
  - "rtx 4080"
  - "rtx 4070"
  - "rtx 4060"
  - "rtx 3080"
  - "rtx 3070"
  - "rtx 3060"
  - "rtx 2070"
  - "rtx 2060"
  - "gtx 1660"
  - "gtx 1080"
  - "radeon 9070"
  - "radeon 9060"
  - "radeon 7900"
  - "radeon 7800"
  - "rx 7600"
  - "rx 6700"
  - "rx 6600"
  - "arc b580"
  - "placa grafica"

attributes:
  perf_index:
    label: "Relative performance (1440p ultra, RTX 5070 Ti = 100)"
    higher_is_better: true
  vram_gb:
    label: "VRAM (GB)"
    higher_is_better: true
  tdp_w:
    label: "Board power (W)"
    higher_is_better: false

rank_by: perf_index

# ---------------------------------------------------------------------------
# Modifiers run against the NORMALIZED title + description: lowercased, accents
# stripped, punctuation removed, "5070ti" split into "5070 ti". Two consequences
# worth remembering when editing these:
#
#   * A pattern containing "/" can never match. "c/ garantia" normalizes to
#     "c garantia", so write it that way.
#   * Portuguese negates with "sem X" / "nao tem X" / "s X". Without a guard,
#     "sem garantia" (NO warranty) matches the warranty pattern and the listing
#     gets an 8% discount for a feature it does not have. Every positive-value
#     modifier below carries the guard.
# ---------------------------------------------------------------------------
modifiers:
  # ---- hard exclusions -------------------------------------------------
  - id: wanted_ad
    patterns: ['^procuro\\b', '^compro\\b', '^pago\\b', '^wanted\\b', '^busco\\b',
               '^troco\\b', '^precisa[- ]se\\b']
    exclude: true
    note: "Buyers, not sellers. These poison the price distribution badly."

  - id: faulty
    patterns:
      ['para pecas', 'para peca', 'avariad', 'nao funciona', 'nao liga',
       'com defeito', 'defeituos', '\\bno funciona', 'averiad', 'sin funcionar',
       '(?<!sem )artefact', 'so para pecas', 'nao da imagem', 'para reparar',
       'para arranjar']
    exclude: true
    note: >-
      The artefact guard matters: "sem artefactos" advertises a HEALTHY card,
      and without the lookbehind it was being thrown away as broken.

  - id: whole_system
    patterns:
      ['pc completo', 'pc gaming completo', 'setup completo', 'torre completa',
       'computador completo', 'equipo completo', 'ordenador completo',
       'pc gamer completo']
    exclude: true
    note: "Full builds need part-out valuation. Out of scope for now."

  - id: mining_rig
    patterns: ['rig de mineracao', 'mining rig', 'rig completo', 'lote de \\d+',
               '\\d+ unidades', 'varias placas']
    exclude: true

  - id: accessory_only
    patterns: ['^waterblock', '^bloco de agua', '^backplate', '^suporte',
               'apenas o cooler', 'so o cooler', 'apenas backplate',
               'suporte anti sag', 'suporte para placa', '^cabo ', '^adaptador ']
    exclude: true
    note: >-
      Accessories carrying a GPU model name. "backplate" is anchored on purpose
      — plenty of genuine listings mention "com backplate" as a selling point,
      and an unanchored pattern threw all of them away.

  # ---- price-normalizing modifiers -------------------------------------
  # Negative pct = the listing carries extra value, so shave it off the asking
  # price to compare fairly against a bare unit.
  - id: warranty
    patterns: ['(?<!sem )(?<!s )garantia', '(?<!sem )garantida', 'warranty',
               '\\bc garantia', 'ainda com gar', '\\bem garantia',
               'con garantia', 'garantia ate']
    price_adjust_pct: -8

  - id: invoice
    patterns: ['(?<!sem )fatura', '(?<!sem )factura', 'talao de compra',
               'nota de compra', 'com recibo', 'con factura']
    price_adjust_pct: -3

  - id: sealed_new
    patterns: ['selad', 'novo por abrir', 'nunca usad', 'lacrad', 'precintad',
               'a estrear', 'sem uso', 'novo na caixa']
    price_adjust_pct: -12

  - id: boxed
    patterns: ['(?<!sem )com caixa', 'na caixa', '(?<!sem )caixa original',
               'con caja']
    price_adjust_pct: -2

  - id: mined_on
    patterns: ['(?<!nunca )usada para mineracao', '(?<!nunca )minou',
               '(?<!sem )(?<!nunca )mineracao', '(?<!nunca )mining',
               'undervolt 24 7']
    price_adjust_pct: 10
    note: >-
      "nunca minou" is a very common selling point, so every pattern here is
      guarded against it. Without the guard the card was penalised for the
      opposite of what the seller wrote.

  - id: no_returns
    patterns: ['sem trocas', 'nao aceito trocas', 'nao troco', 'vendo como esta',
               'sem garantia', 'nao aceito devolucoes']
    price_adjust_pct: 5

  - id: negotiable
    patterns: ['negociavel', 'aceito propostas', 'melhor oferta', 'negociable',
               'aberto a propostas']
    price_adjust_pct: -4
    note: "Asking price overstates the real clearing price."

products:
'''


def quote_list(values: list[str]) -> str:
    return "[" + ", ".join(f'"{v}"' for v in values) + "]"


def render() -> str:
    out = [HEADER]
    groups = [
        ("NVIDIA Blackwell — RTX 50", "rtx_5"),
        ("NVIDIA Ada — RTX 40", "rtx_4"),
        ("NVIDIA Ampere — RTX 30 (not sold new; retail stays 0)", "rtx_3"),
        ("AMD RDNA4 — RX 9000", "rx_9"),
        ("AMD RDNA3 — RX 7000", "rx_7"),
        ("AMD RDNA2 — RX 6000", "rx_6"),
        ("Intel Arc", "arc_"),
        ("Older cards — tracked for price, no verified benchmark", ("rtx_2", "gtx_")),
    ]
    seen: set[str] = set()

    for title, prefix in groups:
        prefixes = prefix if isinstance(prefix, tuple) else (prefix,)
        members = [p for p in P if p[0].startswith(prefixes) and p[0] not in seen]
        if not members:
            continue
        out.append(f"  # {'=' * 24} {title} {'=' * max(2, 40 - len(title))}\n")
        for pid, label, brand, vram, tdp, all_t, any_t, none_t, aliases in members:
            seen.add(pid)
            attrs = []
            pi = perf(pid)
            if pi is not None:
                attrs.append(f"perf_index: {pi}")
            attrs.append(f"vram_gb: {vram}")
            attrs.append(f"tdp_w: {tdp}")

            rule = [f"all: {quote_list(all_t)}"]
            if any_t:
                rule.append(f"any_of: {quote_list(any_t)}")
            rule.append(f"none_of: {quote_list(none_t + LAPTOP)}")

            out.append(f"  - id: {pid}\n")
            out.append(f'    label: "{label}"\n')
            out.append(f"    brand: {brand}\n")
            out.append(f"    attributes: {{{', '.join(attrs)}}}\n")
            out.append(f"    retail_fallback_eur: {retail(pid)}\n")
            out.append(f"    match: {{{', '.join(rule)}}}\n")
            if aliases:
                out.append(f"    aliases: {quote_list(aliases)}\n")
            out.append("\n")
    return "".join(out)


if __name__ == "__main__":
    import sys
    from pathlib import Path

    target = Path(sys.argv[1] if len(sys.argv) > 1 else "catalogs/gpu.yaml")
    target.write_text(render(), encoding="utf-8")
    print(f"wrote {target} — {len(P)} products, "
          f"{sum(1 for p in P if perf(p[0]) is not None)} with verified perf_index")
