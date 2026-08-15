"""Catalog editing.

The catalog is user-editable from the browser, which makes it the most likely
source of a self-inflicted outage. These tests cover the three ways that goes
wrong: writing a file that will not load, silently destroying the comments that
explain the rules, and saving a rule that steals another product's listings.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from scout.normalize import editor

SAMPLE = '''\
category: test
label: "Test things"
currency: EUR

# This comment explains something important and must survive every edit.
query_terms:
  - "widget"

attributes:
  power:
    label: "Power"
    higher_is_better: true
rank_by: power

modifiers:
  # Buyers, not sellers.
  - id: wanted_ad
    patterns: ['^procuro\\b']
    exclude: true

products:
  - id: widget_pro
    label: "Widget Pro"
    brand: acme
    attributes: {power: 100}
    match: {all: ["widget", "pro"], none_of: ["mini"]}
    aliases: ["widgetpro"]

  # The base model. Note the none_of — without it this rule eats the Pro.
  - id: widget
    label: "Widget"
    brand: acme
    attributes: {power: 60}
    match: {all: ["widget"], none_of: ["pro", "mini"]}
'''


@pytest.fixture()
def catalogs(tmp_path, monkeypatch):
    monkeypatch.setattr(editor.settings, "catalog_dir", tmp_path)
    (tmp_path / "test.yaml").write_text(SAMPLE, encoding="utf-8")

    from scout.normalize import catalog as catalog_mod

    catalog_mod.load_all.cache_clear()
    yield tmp_path
    catalog_mod.load_all.cache_clear()


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def test_valid_file_passes(catalogs):
    assert editor.validate_text(SAMPLE).ok


def test_broken_yaml_reports_the_line(catalogs):
    v = editor.validate_text("category: test\nproducts: [ unclosed\n")
    assert not v.ok
    assert "will not parse" in v.errors[0].message
    assert "line" in v.errors[0].where


def test_duplicate_ids_are_an_error(catalogs):
    text = SAMPLE.replace("  - id: widget\n", "  - id: widget_pro\n")
    v = editor.validate_text(text)
    assert any("Duplicate product id" in c.message for c in v.errors)


def test_rule_that_can_never_match_is_an_error(catalogs):
    text = SAMPLE.replace('match: {all: ["widget"], none_of: ["pro", "mini"]}',
                          'match: {none_of: ["pro"]}')
    v = editor.validate_text(text)
    assert any("never match" in c.message for c in v.errors)


def test_bad_modifier_regex_is_an_error(catalogs):
    text = SAMPLE.replace("['^procuro\\b']", "['[unclosed']")
    v = editor.validate_text(text)
    assert any("Bad regex" in c.message for c in v.errors)


def test_unknown_attribute_is_only_a_warning(catalogs):
    text = SAMPLE.replace("{power: 100}", "{power: 100, torque: 5}")
    v = editor.validate_text(text)
    assert v.ok
    assert any("torque" in c.message for c in v.warnings)


# --------------------------------------------------------------------------
# The invariant lint
# --------------------------------------------------------------------------

def test_swallowing_rule_is_caught():
    products = [
        {"id": "base", "match": {"all": ["5080"]}},
        {"id": "variant", "match": {"all": ["5080", "super"]}},
    ]
    checks = editor.lint_variant_swallowing(products)
    assert checks and checks[0].is_error
    assert "'base' will swallow 'variant'" in checks[0].message
    assert "super" in checks[0].message


def test_none_of_silences_the_lint():
    products = [
        {"id": "base", "match": {"all": ["5080"], "none_of": ["super"]}},
        {"id": "variant", "match": {"all": ["5080", "super"]}},
    ]
    assert editor.lint_variant_swallowing(products) == []


def test_unrelated_products_do_not_trip_the_lint():
    products = [
        {"id": "a", "match": {"all": ["5080"]}},
        {"id": "b", "match": {"all": ["9070", "xt"]}},
    ]
    assert editor.lint_variant_swallowing(products) == []


def test_lint_normalizes_before_comparing():
    """`5080Ti` and `5080 ti` are the same rule as far as the matcher is
    concerned, so the lint has to see them that way too."""
    products = [
        {"id": "base", "match": {"all": ["5080"]}},
        {"id": "variant", "match": {"all": ["5080Ti"]}},
    ]
    assert editor.lint_variant_swallowing(products)


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

def test_save_refuses_invalid_text_and_leaves_the_file_alone(catalogs):
    original = editor.read_text("test.yaml")
    result = editor.save_text("test.yaml", "category: test\nproducts: [ broken\n")
    assert not result.ok
    assert editor.read_text("test.yaml") == original


def test_every_save_makes_a_backup(catalogs):
    assert editor.list_backups("test.yaml") == []
    editor.save_text("test.yaml", SAMPLE.replace('"widget"', '"gadget"'))
    assert len(editor.list_backups("test.yaml")) == 1


def test_rapid_saves_do_not_overwrite_each_others_backups(catalogs):
    """Second-resolution timestamps collided, so three quick edits left one
    backup and the version you wanted was gone."""
    for i in range(4):
        editor.save_text("test.yaml", SAMPLE.replace("widget", f"widget{i}"))
    assert len(editor.list_backups("test.yaml")) == 4


def test_restore_round_trips_and_is_itself_undoable(catalogs):
    editor.save_text("test.yaml", SAMPLE + "\n# marker\n")
    backup = editor.list_backups("test.yaml")[0]["name"]
    before = len(editor.list_backups("test.yaml"))

    editor.restore_backup(backup)
    assert "# marker" not in editor.read_text("test.yaml")
    assert len(editor.list_backups("test.yaml")) == before + 1


def test_path_traversal_is_refused(catalogs):
    with pytest.raises(ValueError):
        editor.path_for("../../etc/passwd")
    # A bare name is fine and gets the extension added.
    assert editor.path_for("gpu").name == "gpu.yaml"


# --------------------------------------------------------------------------
# Structured edits must not destroy the file
# --------------------------------------------------------------------------

def test_adding_a_product_preserves_comments(catalogs):
    text = editor.read_text("test.yaml")
    before = text.count("#")
    updated = editor.upsert_product(text, {
        "id": "widget_mini", "label": "Widget Mini",
        "attributes": {"power": 20},
        "match": {"all": ["widget", "mini"], "none_of": ["pro"]},
    })
    assert updated.count("#") == before
    assert "important and must survive" in updated
    assert "widget_mini" in updated
    assert editor.validate_text(updated).ok


def test_editing_a_product_replaces_only_that_block(catalogs):
    text = editor.read_text("test.yaml")
    updated = editor.upsert_product(text, {
        "id": "widget", "label": "Widget (renamed)",
        "attributes": {"power": 61},
        "match": {"all": ["widget"], "none_of": ["pro", "mini"]},
    })
    assert "Widget (renamed)" in updated
    assert "Widget Pro" in updated, "the neighbouring product was damaged"
    assert "must survive every edit" in updated
    import yaml
    products = yaml.safe_load(updated)["products"]
    assert len(products) == 2


def test_deleting_a_product_leaves_the_rest_intact(catalogs):
    text = editor.read_text("test.yaml")
    updated = editor.delete_product(text, "widget_pro")
    import yaml
    products = yaml.safe_load(updated)["products"]
    assert [p["id"] for p in products] == ["widget"]
    assert "must survive every edit" in updated


def test_deleting_an_unknown_product_raises(catalogs):
    with pytest.raises(KeyError):
        editor.delete_product(editor.read_text("test.yaml"), "nope")


def test_rendered_product_round_trips_through_yaml():
    import yaml

    product = {
        "id": "thing_x", "label": 'A "quoted" thing', "brand": "acme",
        "attributes": {"power": 100.0, "mass": 2.5},
        "retail_fallback_eur": 499,
        "match": {"all": ["thing", "x"], "any_of": [], "none_of": ["mini"]},
        "aliases": ["thingx"],
    }
    parsed = yaml.safe_load("products:\n" + editor.render_product(product))["products"][0]
    assert parsed["id"] == "thing_x"
    assert parsed["label"] == 'A "quoted" thing'
    assert parsed["attributes"] == {"power": 100, "mass": 2.5}
    assert parsed["match"]["all"] == ["thing", "x"]
    assert parsed["match"]["none_of"] == ["mini"]
    assert "any_of" not in parsed["match"], "empty lists should be omitted"


def test_upsert_into_an_empty_products_list(catalogs):
    """The new-category template ships `products: []`."""
    (catalogs / "empty.yaml").write_text(
        "category: empty\nquery_terms: [\"x\"]\nmodifiers: []\nproducts: []\n",
        encoding="utf-8")
    text = editor.read_text("empty.yaml")
    updated = editor.upsert_product(text, {
        "id": "first", "label": "First", "match": {"all": ["first"]}})
    import yaml
    assert [p["id"] for p in yaml.safe_load(updated)["products"]] == ["first"]


# --------------------------------------------------------------------------
# New categories & resilience
# --------------------------------------------------------------------------

def test_create_category_produces_a_loadable_file(catalogs):
    name = editor.create_category("Road Bikes", "Road bicycles")
    assert name == "road_bikes.yaml"
    v = editor.validate_text(editor.read_text(name))
    assert v.ok, [c.message for c in v.errors]

    from scout.normalize import catalog as catalog_mod

    assert "road_bikes" in catalog_mod.reload()


def test_create_category_refuses_duplicates(catalogs):
    editor.create_category("bikes")
    with pytest.raises(FileExistsError):
        editor.create_category("bikes")


def test_a_broken_file_does_not_take_down_the_other_catalogs(catalogs):
    """A user saving nonsense by hand must cost one category, not the app."""
    (catalogs / "broken.yaml").write_text("category: broken\nproducts: [ oops\n",
                                          encoding="utf-8")
    from scout.normalize import catalog as catalog_mod

    loaded = catalog_mod.reload()
    assert "test" in loaded, "the healthy catalog should still load"
    assert "broken.yaml" in catalog_mod.LOAD_ERRORS
