# Prior session (c952c964) final state - what was already learned

2026-04-20 ~15:23 PT. Original: `git show b012073:investigation/1776724600_03_prior_session_summary.md`.

## What the prior session confirmed
Teeing the JSON-RPC traffic through an `MCP_RPC_LOG` env var showed Claude Code
Desktop (CCD, `clientInfo.name = local-agent-mode-<server>`) initializes with only
`{"roots":{"listChanged":true}}`. It does **not** advertise
`extensions.io.modelcontextprotocol/ui`. Claude Desktop chat (`claude-ai`) supports
MCP Apps iframes, gated by the `claudeai_mcp_a6k_enabled` GrowthBook flag.

## What was implemented
- **Path A, Claude Desktop chat.** `_meta.ui.resourceUri` plus `registerAppResource`
  renders the iframe.
- **Path B, CCD.** Every tool call regenerates a self-contained dark HTML page, and the
  tool description tells Claude to apply a trivial `Edit` so the `PostToolUse:Edit`
  hook opens it in the Launch preview panel. Verified end to end with a grid of 8
  projects and click-through cards.

Path B is what "worked a week ago", not an MCP Apps iframe.

## The current question

Kai's session `dcef779b` wants an Amplitude chart inline in CCD. Amplitude's
`shouldRenderUI: true` is a custom field, not MCP Apps spec. Even with
`_meta.ui.resourceUri`, CCD has no iframe renderer, so the only route is a Path B style
fallback: a wrapper that writes the chart as HTML and asks Claude to `Edit` it, or a
chart image embedded as a markdown image (does CCD render those? unchecked).

## Open questions

1. Does Amplitude MCP have an `export_chart_image` or equivalent?
2. Does the Launch preview render markdown or images, as well as HTML?
3. Do other Amplitude MCP tools hide render options?

## Next step

Scan session `f3a122e0` (started 14:25) for other rendering approaches. Check whether
`render_chart` returns a preview URL (only `chartEditUrl`, an amplitude.com link, was
seen). Check the other Amplitude tools for image export.
