"""The frontend's annotated norm fixtures match what the annotator produces now."""

from __future__ import annotations

from scripts import frontend_norm_fixtures as fx


def test_committed_frontend_norm_fixtures_match_the_annotator() -> None:
    for name, text in fx.build().items():
        committed = (fx.TARGET / name).read_text()
        assert committed == text, f"{name} drifted, run `just frontend-norm-fixtures`"
