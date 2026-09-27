"""Shared fixtures for the tests/mcp suites."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in `frontend/dist` so the SPA catch-all has a shell to serve.

    `create_app()` reads `FRONTEND_DIST` at call time and defaults to
    `frontend/dist`, which only exists after a frontend build. CI's `test` job
    never builds one - the frontend is a separate job - so a test that expects
    the catch-all to serve the shell has to bring its own dist. Without it the
    fallback returns the no-build 404 instead, which passes a developer machine
    with a stale local build and fails in CI.
    """
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html>\n<html>spa-shell</html>")
    (dist / "robots.txt").write_text("crawl away")
    monkeypatch.setenv("FRONTEND_DIST", str(dist))
    return dist


@pytest.fixture(autouse=True)
def fixed_norms_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """Price tools read the live cycle and stage for their norms. Tests pin them
    rather than reaching the live server (teable:coilyco/eco-app#8368)."""
    from eco_mcp_app import norms

    async def fixed(server: str | None, **_: object) -> norms.LiveContext:
        return norms.LiveContext(stage="Modern 4", cycle=14, home=server in (None, ""))

    monkeypatch.setattr(norms, "live_context", fixed)
