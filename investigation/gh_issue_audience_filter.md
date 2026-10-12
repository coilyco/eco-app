# Widget renders empty in Claude Desktop after `audience=["user"]` on the HTMX block

Draft issue for eco-mcp-app. Original: `git show b012073:investigation/gh_issue_audience_filter.md`.

## Symptom

In Claude Desktop, a tool that ships an HTMX widget fragment (e.g. `get_server_status`)
renders only the iframe's empty state ("Ask Claude to check the server status."). The
model still gets the markdown and JSON blocks, so the chat reply has the right data.

## Cause

Commit `7542e67` annotated the `HTMX:`-prefixed `TextContent` block with
`Annotations(audience=["user"])` (`_htmx_content()` in `src/eco_mcp_app/server.py`),
assuming the iframe reads the block by prefix, not annotation. That looks wrong:
Claude Desktop seems to strip `audience=["user"]` blocks from the
`ui/notifications/tool-result` payload it forwards to the iframe, not only from the
model context. Then `extractHtmlFragment()` in `templates/eco.html` finds no `HTMX:`
text, returns `null`, and `onToolResult` exits before replacing `partials/empty.html`.

Reproduce: run the server at `7542e67` or later in Claude Desktop and call
`get_server_status`. To verify, remove `annotations=_WIDGET_AUDIENCE` from
`_htmx_content()`: if the widget hydrates, the hypothesis holds.

## Proposed fix

Move the fragment out of `content` and into `_meta.ui.fragment`, which compliant hosts
do not forward to the model. That keeps ~70KB of inlined-image HTML out of the model's
context without relying on host audience semantics. `extractHtmlFragment()` reads
`result._meta.ui.fragment` first and falls back to the `HTMX:` content scan for one
release, so older tool results still render.

Rejected: `Annotations(priority=0.0)` (advisory only) and per-host annotation (the server
cannot see host capabilities at call time).

Related: finding #5 belongs in `1776735420_08_eco_mcp_app_debug_chain.md` once resolved.
`static/harness.html` line 24 still points at `ui/eco.html`, which moved to
`templates/eco.html` in `de5d899`, so the harness is broken.
