# eco-mcp-app debug chain - what the sibling investigation could not tell us

2026-04-20 ~18:00 PT. Original with code: `git show b012073:investigation/1776735420_08_eco_mcp_app_debug_chain.md`.

A second MCP App, `eco-mcp-app` (Python, hand-rolled handshake, no ext-apps SDK), exposed
four things the SDK hid.

## 1. `ui/initialize` params are not what #61 shows
@ochafik's snippet sends `{protocolVersion, capabilities}`. The real schema, read from
`node_modules/@modelcontextprotocol/ext-apps/dist/src/app.js`, requires
`{appInfo: {name, version}, appCapabilities, protocolVersion: "2026-01-26"}`
(`McpUiInitializeRequestSchema`). With the short form the host rejects the request,
the View never sends `ui/notifications/initialized`, and the container stays hidden.
It looks like "MCP Apps is broken". Fixed in `3d2b30f`.

## 2. Sizing is `ui/notifications/size-changed`, not documentElement height
Issue #69 says claude.ai reads `documentElement.height`. Claude Desktop 1.3561 does
not: CSS height, inline height and a script-set height all left the iframe clipped at
~280px, and only a `size-changed {width, height}` after `initialized` resized it. The
SDK sends it on init, measures with `height: max-content`, and keeps a `ResizeObserver`
on `documentElement` and `body`. Fixed in `20a3448` by mirroring that. #69 may be
version-specific, it was filed in February 2026.

## 3. Python SDK `read_resource` returns `list[ReadResourceContents]`
`@server.read_resource()` in `mcp` 1.14+ needs
`mcp.server.lowlevel.helper_types.ReadResourceContents`, not `ReadResourceResult`.
The wrong type fails with `'tuple' object has no attribute 'content'` and the host shows
"Unable to reach eco-mcp-app" although the tool call succeeded. Fixed in `ac48279`.

## 4. A local harness beats blind iteration
Four commits went to "edit, quit Claude Desktop, relaunch, test". A ~100 line harness
(`static/harness.html`) that mimics the host (answers `ui/initialize`, reveals on
`initialized`, applies `size-changed`, pushes a canned tool result) exposed a silent
`SyntaxError: Identifier 'pending' has already been declared` in seconds, invisible
from the parent window. Worth borrowing for the sibling, which had no equivalent.

Commits in eco-mcp-app: `ac48279`, `3d2b30f`, `20a3448` are the fixes, `3bf123b`, `f355888`
and `741b8ca` were wrong sizing theories. Next: add findings 1 and 2 to `issue_61_reply_draft.md`.
