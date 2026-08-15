"""Catalog editing, safely.

The catalog is the one file a user is expected to change regularly, and it is
also the file that can silently ruin every price distribution in the database.
So editing goes through here, and here does four things nothing else does:

1. **Validates before writing.** A file that would not load is never written.
2. **Backs up every write**, so any mistake is one click from undone.
3. **Preserves comments and formatting.** Structured edits splice the YAML text
   rather than re-serialising the document — a round-trip through PyYAML would
   strip every comment in the file, and the comments are where the reasoning
   lives.
4. **Lints the variant-swallowing invariant.** An "RTX 5070" rule that forgets
   to exclude "ti" quietly corrupts two products' price histories and raises no
   error anywhere. That check is the whole reason this module has opinions.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ..config import settings
from .text import normalize

BACKUP_DIR = ".backups"
KEEP_BACKUPS = 20

TEMPLATE = """\
category: {category}
label: "{label}"
currency: EUR

# What the collectors search for. Keep these broad — the matcher does the
# precision work, and fewer/wider terms means fewer requests.
query_terms:
  - "{label_lower}"

# Numeric attributes used for cross-product value ranking. `rank_by` is the one
# the compare view divides price by. Leave both empty if the category has no
# meaningful "performance per euro".
attributes: {{}}
rank_by: ""

# Regex over the normalized title + description. `exclude: true` throws the
# listing away; `price_adjust_pct` normalizes the price so listings compare
# like-for-like (negative = the listing is worth more than its price suggests).
modifiers:
  - id: wanted_ad
    patterns: ['^procuro\\b', '^compro\\b', '^wanted\\b']
    exclude: true
    note: "Buyers, not sellers. These poison the price distribution badly."

products: []
"""


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------

def catalog_dir() -> Path:
    return Path(settings.catalog_dir)


def path_for(name: str) -> Path:
    """Resolve a catalog filename inside the catalog directory.

    Refuses separators and `..` outright rather than quietly stripping them —
    silently turning `../../etc/passwd` into `passwd.yaml` is confined, but it
    is also the kind of surprise that hides a real mistake.
    """
    raw = (name or "").strip()
    if not raw or "/" in raw or "\\" in raw or ".." in raw:
        raise ValueError(f"invalid catalog filename: {name!r}")
    if not raw.endswith((".yaml", ".yml")):
        raw += ".yaml"
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", raw):
        raise ValueError(f"invalid catalog filename: {name!r}")
    return catalog_dir() / raw


def list_files() -> list[dict]:
    out = []
    for p in sorted(catalog_dir().glob("*.yaml")):
        stat = p.stat()
        out.append({
            "name": p.name,
            "bytes": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            "writable": _is_writable(p),
        })
    return out


def _is_writable(path: Path) -> bool:
    """True if we could actually save this file.

    The compose file used to mount ./catalogs read-only, which made every save
    fail at the last step. Better to grey the button out and say why.
    """
    import os

    return os.access(path, os.W_OK) and os.access(path.parent, os.W_OK)


def read_text(name: str) -> str:
    return path_for(name).read_text(encoding="utf-8")


def file_for_category(category: str) -> str | None:
    for p in sorted(catalog_dir().glob("*.yaml")):
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if data.get("category") == category:
            return p.name
    return None


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

@dataclass(slots=True)
class Check:
    level: str          # error | warning | info
    message: str
    where: str = ""

    @property
    def is_error(self) -> bool:
        return self.level == "error"


@dataclass(slots=True)
class Validation:
    checks: list[Check] = field(default_factory=list)
    parsed: dict | None = None

    @property
    def ok(self) -> bool:
        return not any(c.is_error for c in self.checks)

    @property
    def errors(self) -> list[Check]:
        return [c for c in self.checks if c.level == "error"]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.level == "warning"]


def validate_text(text: str) -> Validation:
    """Everything that can be checked without touching the database."""
    v = Validation()

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        v.checks.append(Check("error", f"YAML will not parse: {exc}", where))
        return v

    if not isinstance(data, dict):
        v.checks.append(Check("error", "Top level must be a mapping."))
        return v
    v.parsed = data

    if not data.get("category"):
        v.checks.append(Check("error", "Missing required key: category"))
    if not isinstance(data.get("products", []), list):
        v.checks.append(Check("error", "`products` must be a list"))
        return v

    products = data.get("products") or []
    modifiers = data.get("modifiers") or []
    attributes = data.get("attributes") or {}
    rank_by = data.get("rank_by") or ""

    if not data.get("query_terms"):
        v.checks.append(Check("warning", "No query_terms — the collector has "
                                         "nothing broad to search for."))
    if rank_by and rank_by not in attributes:
        v.checks.append(Check("warning",
                              f"rank_by '{rank_by}' is not in attributes, so the "
                              f"compare view will rank everything at zero."))

    # --- modifiers ---
    for i, m in enumerate(modifiers):
        where = f"modifiers[{i}] ({m.get('id', '?')})"
        if not m.get("id"):
            v.checks.append(Check("error", "Modifier has no id", where))
        for pattern in m.get("patterns", []) or []:
            try:
                re.compile(pattern)
            except re.error as exc:
                v.checks.append(Check("error", f"Bad regex {pattern!r}: {exc}", where))
        if not m.get("patterns"):
            v.checks.append(Check("warning", "Modifier has no patterns", where))

    # --- products ---
    seen_ids: dict[str, int] = {}
    for i, p in enumerate(products):
        pid = p.get("id")
        where = f"products[{i}] ({pid or '?'})"
        if not pid:
            v.checks.append(Check("error", "Product has no id", where))
            continue
        if pid in seen_ids:
            v.checks.append(Check(
                "error",
                f"Duplicate product id '{pid}' (also at products[{seen_ids[pid]}]). "
                f"Ids are database keys — two rows would fight over one history.",
                where))
        seen_ids[pid] = i

        rule = p.get("match") or {}
        if not (rule.get("all") or rule.get("any_of")):
            v.checks.append(Check(
                "error",
                "Rule has neither `all` nor `any_of`, so it can never match.",
                where))
        for key in ("all", "any_of", "none_of"):
            if key in rule and not isinstance(rule[key], list):
                v.checks.append(Check("error", f"match.{key} must be a list", where))

        for attr in (p.get("attributes") or {}):
            if attr not in attributes:
                v.checks.append(Check(
                    "warning",
                    f"attribute '{attr}' is not declared in the top-level "
                    f"`attributes` block", where))
        if rank_by and not (p.get("attributes") or {}).get(rank_by):
            v.checks.append(Check(
                "info",
                f"no '{rank_by}' value — this product is excluded from the "
                f"value ranking", where))

    v.checks.extend(lint_variant_swallowing(products))
    return v


def lint_variant_swallowing(products: list[dict]) -> list[Check]:
    """The invariant that matters more than any other.

    If product A requires {"5070"} and product B requires {"5070", "ti"}, then
    A matches every B listing unless A excludes "ti". When that happens the
    5070 distribution is polluted upward by Ti prices and the Ti distribution
    loses its cheap end — and nothing anywhere raises an error. Every deal
    derived from either product is then wrong.
    """
    def token_set(values) -> set[str]:
        """Normalize, then split into individual words.

        `normalize("5080Ti")` yields "5080 ti" — two tokens. Keeping that as a
        single set member let an author evade the whole check just by writing
        the model without a space, which is exactly how people type it.
        """
        out: set[str] = set()
        for value in values or []:
            out.update(normalize(value).split())
        return out

    checks: list[Check] = []
    rules = []
    for p in products:
        pid = p.get("id")
        rule = p.get("match") or {}
        if not pid or not isinstance(rule.get("all", []), list):
            continue
        rules.append((
            pid,
            token_set(rule.get("all")),
            token_set(rule.get("none_of")),
        ))

    for pid_a, all_a, none_a in rules:
        for pid_b, all_b, _none_b in rules:
            if pid_a == pid_b or not all_a or not all_b:
                continue
            # B is strictly more specific than A.
            if all_a < all_b:
                distinguishing = all_b - all_a
                if not distinguishing & none_a:
                    missing = ", ".join(sorted(distinguishing))
                    checks.append(Check(
                        "error",
                        f"'{pid_a}' will swallow '{pid_b}' listings: it requires "
                        f"{sorted(all_a)} and does not exclude {missing}. "
                        f"Add none_of: [{missing}] to '{pid_a}'.",
                        f"products / {pid_a}",
                    ))
    return checks


def validate_for_save(text: str) -> Validation:
    """Validation plus a real load through the production code path.

    Structural checks can pass while the actual loader still trips over
    something, so the last gate is: build the Catalog object the app will use.
    """
    v = validate_text(text)
    if not v.ok:
        return v
    try:
        from . import catalog as catalog_mod

        catalog_mod._load_file  # noqa: B018 - presence check
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(text)
            tmp = Path(fh.name)
        try:
            cat = catalog_mod._load_file(tmp)
            v.checks.append(Check(
                "info",
                f"Loads cleanly: {len(cat.products)} products, "
                f"{len(cat.modifiers)} modifiers, {len(cat.query_terms)} search terms."))
        finally:
            tmp.unlink(missing_ok=True)
    except Exception as exc:
        v.checks.append(Check("error", f"Loader rejected the file: "
                                       f"{type(exc).__name__}: {exc}"))
    return v


# --------------------------------------------------------------------------
# Writing & backups
# --------------------------------------------------------------------------

def backup_dir() -> Path:
    d = catalog_dir() / BACKUP_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def make_backup(name: str) -> Path | None:
    src = path_for(name)
    if not src.exists():
        return None
    # Millisecond precision, not seconds: editing through a web form produces
    # several saves inside the same second (add a product, fix a typo, delete
    # it), and a second-resolution name silently overwrote the very backup you
    # would want to go back to.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%f")[:-3] + "Z"
    dest = backup_dir() / f"{src.stem}.{stamp}.yaml"
    if dest.exists():  # same millisecond; vanishingly rare but free to handle
        for n in range(1, 100):
            candidate = backup_dir() / f"{src.stem}.{stamp}-{n}.yaml"
            if not candidate.exists():
                dest = candidate
                break
    shutil.copy2(src, dest)
    _prune_backups(src.stem)
    return dest


def _prune_backups(stem: str) -> None:
    backups = sorted(backup_dir().glob(f"{stem}.*.yaml"), reverse=True)
    for old in backups[KEEP_BACKUPS:]:
        old.unlink(missing_ok=True)


def list_backups(name: str) -> list[dict]:
    stem = path_for(name).stem
    out = []
    for p in sorted(backup_dir().glob(f"{stem}.*.yaml"), reverse=True):
        stat = p.stat()
        out.append({
            "name": p.name,
            "bytes": stat.st_size,
            "saved": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        })
    return out


def restore_backup(backup_name: str) -> str:
    """Restore a backup over its live file. Backs up the current state first,
    so restoring is itself undoable."""
    src = backup_dir() / Path(backup_name).name
    if not src.exists():
        raise FileNotFoundError(backup_name)
    stem = src.name.split(".")[0]
    target = f"{stem}.yaml"
    make_backup(target)
    path_for(target).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def save_text(name: str, text: str, validate: bool = True) -> Validation:
    """Validate, back up, then write. Never writes a file that would not load."""
    v = validate_for_save(text) if validate else Validation()
    if validate and not v.ok:
        return v
    if not text.endswith("\n"):
        text += "\n"
    make_backup(name)
    path_for(name).write_text(text, encoding="utf-8")

    from . import catalog as catalog_mod

    catalog_mod.reload()
    return v


# --------------------------------------------------------------------------
# Structured edits that keep the file's comments intact
# --------------------------------------------------------------------------

def _product_block(lines: list[str], pid: str) -> tuple[int, int] | None:
    """Line span [start, end) of one product entry in the raw text."""
    start = None
    indent = 0
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*)-\s+id:\s*['\"]?([A-Za-z0-9_\-]+)['\"]?\s*$", line)
        if m and m.group(2) == pid:
            start = i
            indent = len(m.group(1))
            break
    if start is None:
        return None
    for j in range(start + 1, len(lines)):
        line = lines[j]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cur = len(line) - len(line.lstrip())
        if cur < indent or (cur == indent and line.lstrip().startswith("- ")):
            return start, j
    return start, len(lines)


def render_product(product: dict, indent: str = "  ") -> str:
    """Emit one product as YAML, in the same shape as the hand-written file."""
    def q(value: str) -> str:
        return '"' + str(value).replace('"', '\\"') + '"'

    def seq(values) -> str:
        return "[" + ", ".join(q(v) for v in values) + "]"

    lines = [f"{indent}- id: {product['id']}",
             f"{indent}  label: {q(product.get('label', product['id']))}"]
    if product.get("brand"):
        lines.append(f"{indent}  brand: {product['brand']}")
    attrs = {k: v for k, v in (product.get("attributes") or {}).items()
             if v not in (None, "")}
    if attrs:
        body = ", ".join(f"{k}: {_num(v)}" for k, v in attrs.items())
        lines.append(f"{indent}  attributes: {{{body}}}")
    if product.get("retail_fallback_eur"):
        lines.append(f"{indent}  retail_fallback_eur: "
                     f"{_num(product['retail_fallback_eur'])}")

    rule = product.get("match") or {}
    parts = []
    for key in ("all", "any_of", "none_of"):
        vals = [v for v in (rule.get(key) or []) if str(v).strip()]
        if vals:
            parts.append(f"{key}: {seq(vals)}")
    lines.append(f"{indent}  match: {{{', '.join(parts)}}}")

    aliases = [a for a in (product.get("aliases") or []) if str(a).strip()]
    if aliases:
        lines.append(f"{indent}  aliases: {seq(aliases)}")
    return "\n".join(lines) + "\n"


def _num(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return value
    return int(f) if f.is_integer() else f


def upsert_product(text: str, product: dict) -> str:
    """Add or replace one product, leaving every other byte of the file alone."""
    lines = text.splitlines(keepends=True)
    span = _product_block(lines, product["id"])
    block = render_product(product)

    if span:
        start, end = span
        return "".join(lines[:start]) + block + "".join(lines[end:])

    # Append under `products:`.
    for i, line in enumerate(lines):
        if re.match(r"^products:\s*(\[\s*\])?\s*$", line):
            # An empty `products: []` becomes a real list.
            head = "".join(lines[:i]) + "products:\n"
            return head + block + "".join(lines[i + 1:])
    return text.rstrip("\n") + "\n\nproducts:\n" + block


def delete_product(text: str, pid: str) -> str:
    lines = text.splitlines(keepends=True)
    span = _product_block(lines, pid)
    if not span:
        raise KeyError(f"product {pid!r} not found in this file")
    start, end = span
    return "".join(lines[:start]) + "".join(lines[end:])


def create_category(name: str, label: str = "") -> str:
    """Write a new catalog file from the template. Returns the filename."""
    slug = re.sub(r"[^a-z0-9_]+", "_", name.strip().lower()).strip("_")
    if not slug:
        raise ValueError("category name must contain a letter or digit")
    target = path_for(slug)
    if target.exists():
        raise FileExistsError(f"{target.name} already exists")
    text = TEMPLATE.format(category=slug, label=label or slug.replace("_", " ").title(),
                           label_lower=(label or slug).lower())
    target.write_text(text, encoding="utf-8")

    from . import catalog as catalog_mod

    catalog_mod.reload()
    return target.name


# --------------------------------------------------------------------------
# Dry-run a rule against listings you have already collected
# --------------------------------------------------------------------------

@dataclass(slots=True)
class RulePreview:
    matched: int = 0
    samples: list[dict] = field(default_factory=list)
    steals: list[dict] = field(default_factory=list)
    scanned: int = 0

    @property
    def steal_count(self) -> int:
        return len(self.steals)


def preview_rule(category: str, all_of: list[str], any_of: list[str],
                 none_of: list[str], product_id: str | None = None,
                 limit: int = 10) -> RulePreview:
    """Show what a rule would match, before it is saved.

    This is the antidote to the silent-corruption failure mode: instead of
    saving a rule and discovering three weeks later that it ate a neighbouring
    product's listings, you see the damage up front, including exactly which
    listings it would take from which product.
    """
    from ..db import query
    from .text import has_token, tokens_of

    req = [normalize(t) for t in all_of if str(t).strip()]
    opt = [normalize(t) for t in any_of if str(t).strip()]
    ban = [normalize(t) for t in none_of if str(t).strip()]
    preview = RulePreview()
    if not (req or opt):
        return preview

    rows = query(
        """
        SELECT n.normalized_title, n.product_id, l.title, l.price_cents
        FROM normalized n JOIN listing l ON l.id = n.listing_id
        WHERE n.category = %s AND n.match_kind <> 'excluded'
          AND n.normalized_title <> ''
        ORDER BY n.updated_at DESC
        LIMIT 4000
        """,
        (category,),
    )
    preview.scanned = len(rows)

    for r in rows:
        norm = r["normalized_title"]
        toks = tokens_of(norm)
        if any(not has_token(norm, toks, t) for t in req):
            continue
        if opt and not any(has_token(norm, toks, t) for t in opt):
            continue
        if any(has_token(norm, toks, t) for t in ban):
            continue

        preview.matched += 1
        owner = r["product_id"]
        entry = {
            "title": r["title"][:90],
            "price_eur": (r["price_cents"] or 0) / 100.0,
            "current": owner,
        }
        if owner and owner != product_id:
            if len(preview.steals) < limit:
                preview.steals.append(entry)
        elif len(preview.samples) < limit:
            preview.samples.append(entry)

    return preview
