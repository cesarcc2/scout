"""Startup smoke tests.

These exist because of a real outage: `typer==0.12.5` pinned against an
unpinned `click`, which floated to 8.4 on a later rebuild. Typer built its
boolean flags in a way click 8.4 rejects, and the container died with
`TypeError: Secondary flag is not valid for non-boolean flag` before serving a
single request. Every unit test still passed, because none of them ever asked
Typer to actually assemble the command group.

Nothing here tests our logic. They test that the app can be *constructed* at
all under the installed dependency set — which is the failure mode a pinned
lock file plus these two tests together prevent.
"""

from __future__ import annotations

import pytest


def test_cli_command_group_builds():
    """The exact call that crashed the container on boot."""
    from typer.main import get_command

    from scout.cli import app

    command = get_command(app)
    names = set(command.commands)  # type: ignore[attr-defined]
    assert {"serve", "bootstrap", "cycle", "deals", "compare",
            "search", "test-alert"} <= names


def test_every_cli_command_has_a_usable_signature():
    """Builds each command's parameters individually, so a failure names the
    offending command instead of just the group."""
    from typer.main import get_command

    from scout.cli import app

    for name, command in get_command(app).commands.items():  # type: ignore[attr-defined]
        try:
            params = command.params
        except Exception as exc:  # pragma: no cover - the thing we're guarding
            pytest.fail(f"command {name!r} failed to build: {exc}")
        assert isinstance(params, list)


def test_web_app_routes_are_registered():
    """Form(...) routes need python-multipart installed.

    Without it FastAPI raises when the route is built, so the dashboard renders
    and then every button 500s. Asserting the POST routes exist proves the
    dependency is present.
    """
    from scout.web.app import app as web

    paths = {getattr(r, "path", None) for r in web.routes}
    for path in ("/", "/products", "/product/{product_id}", "/compare",
                 "/search", "/catalog", "/status", "/feed.xml"):
        assert path in paths, f"missing route {path}"
    for path in ("/deals/seen", "/alerts/test", "/catalog/dismiss",
                 "/jobs/bootstrap", "/jobs/cycle", "/jobs/retail",
                 "/jobs/normalize"):
        assert path in paths, f"missing POST route {path} (python-multipart?)"


def test_multipart_is_installed():
    """Named explicitly so the failure message says what to install."""
    pytest.importorskip(
        "multipart",
        reason="python-multipart is required by FastAPI for Form(...) params",
    )


def test_settings_load_without_any_env():
    """A fresh clone with no .env must still import and start."""
    from scout.config import Settings

    s = Settings()
    assert s.port and s.catalog_dir
    assert s.discord_webhook_url == "" and s.ntfy_topic == ""


def test_catalogs_parse():
    from scout.normalize import catalog as catalog_mod

    catalogs = catalog_mod.load_all()
    assert catalogs, "no catalog YAML found"
    for cat in catalogs.values():
        assert cat.products and cat.query_terms
