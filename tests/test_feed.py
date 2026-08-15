"""The RSS feed is the no-configuration substitute for push notifications, so
it has to be well-formed even when a listing title is full of XML metacharacters.
"""

from __future__ import annotations

import html
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from scout.web import app as webapp
from tests.test_notify import make_deal


@pytest.fixture()
def client(monkeypatch):
    deals = [
        make_deal(listing_id=1),
        make_deal(listing_id=2, title='Placa & "gráfica" <b>RTX</b> 5070 Ti',
                  product_label="GeForce RTX 5070 Ti", location="Braga & Porto",
                  retail_eur=None, retail_ratio=None,
                  cautions=["listed 61 days & still unsold"]),
    ]
    monkeypatch.setattr(webapp, "find_deals", lambda *a, **k: deals)
    return TestClient(webapp.app)


def test_feed_is_well_formed_xml(client):
    r = client.get("/feed.xml")
    assert r.status_code == 200
    assert "application/rss+xml" in r.headers["content-type"]
    root = ET.fromstring(r.content)          # raises on malformed XML
    assert root.get("version") == "2.0"
    assert len(root.findall("./channel/item")) == 2


def test_metacharacters_in_a_listing_do_not_break_the_feed(client):
    """A seller writing `<b>` in their ad title must not produce a feed that
    every reader rejects.

    The raw bytes must be escaped; once parsed, the original characters have to
    come back intact rather than mangled.
    """
    raw = client.get("/feed.xml").text
    # The seller's markup must never become live markup in a reader.
    assert "<b>RTX</b>" not in raw, "unescaped markup leaked into the feed"
    assert "&lt;b&gt;RTX&lt;/b&gt;" in raw

    # XML parses, and the text survives the round trip unmangled.
    root = ET.fromstring(raw.encode())
    desc = html.unescape(root.findall("./channel/item")[1].find("description").text)
    assert 'Placa & "gráfica" <b>RTX</b> 5070 Ti' in desc
    assert "Braga & Porto" in desc
    assert "listed 61 days & still unsold" in desc


def test_items_carry_link_and_stable_guid(client):
    root = ET.fromstring(client.get("/feed.xml").content)
    for item in root.findall("./channel/item"):
        assert item.find("link").text.startswith("https://")
        guid = item.find("guid")
        # Price is part of the guid, so a price drop re-surfaces in the reader.
        assert guid.text.startswith("scout-")
        assert guid.get("isPermaLink") == "false"


def test_missing_retail_omits_that_line(client):
    root = ET.fromstring(client.get("/feed.xml").content)
    desc = root.findall("./channel/item")[1].find("description").text
    assert "retail price" not in desc
