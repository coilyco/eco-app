"""Rewrite the SPA shell's `<head>` for one URL, so a preview scraper sees its words.

Link-preview scrapers read `<head>` and run no script, so the title,
description and `og:*` tags must differ per route in the served HTML. The body
is never touched, which keeps every route's body identical and the
duplicate-content protection in docs/frontend/crawl-surface.md intact.
"""

from __future__ import annotations

import html
import re

from .seo import Head

# A tag body that may hold `>` inside a quoted attribute value.
_TAG_BODY = r"""(?:"[^"]*"|'[^']*'|[^>"'])*"""
_TITLE = re.compile(r"<title\b[^>]*>.*?</title>", re.IGNORECASE | re.DOTALL)


def _meta(attr: str, name: str) -> re.Pattern[str]:
    return re.compile(
        rf"""<meta\b(?=[^>]*\b{attr}\s*=\s*["']{re.escape(name)}["']){_TAG_BODY}>""",
        re.IGNORECASE,
    )


def _set_meta(doc: str, attr: str, name: str, value: str) -> str:
    """Replace the tag, or add it before `</head>` when the build dropped it."""
    tag = f'<meta {attr}="{name}" content="{html.escape(value, quote=True)}" />'
    pattern = _meta(attr, name)
    if pattern.search(doc):
        return pattern.sub(lambda _: tag, doc, count=1)
    closing = re.compile(r"</head>", re.IGNORECASE)
    return closing.sub(lambda m: f"    {tag}\n  {m.group(0)}", doc, count=1)


def apply(doc: str, head: Head) -> str:
    """The shell with `head` applied. A field that is None keeps the shell's default,
    except `url`, whose tag is removed so the shell's home-page default names no page."""
    if head.title:
        title = f"<title>{html.escape(head.title, quote=False)}</title>"
        doc = _TITLE.sub(lambda _: title, doc, count=1)
        doc = _set_meta(doc, "property", "og:title", head.title)
    if head.description:
        doc = _set_meta(doc, "name", "description", head.description)
        doc = _set_meta(doc, "property", "og:description", head.description)
    if head.url:
        doc = _set_meta(doc, "property", "og:url", head.url)
    else:
        dropped = rf"[ \t]*{_meta('property', 'og:url').pattern}[ \t]*\n?"
        doc = re.sub(dropped, "", doc, count=1, flags=re.IGNORECASE)
    return doc
