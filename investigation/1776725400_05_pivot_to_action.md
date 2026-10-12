# Pivot from investigation to action

2026-04-20 ~15:37 PT. Original: `git show b012073:investigation/1776725400_05_pivot_to_action.md`.

## What was confirmed

1. The sibling MCP App server is correct. A stdio smoke test (as `claude-ai`, with the
   `io.modelcontextprotocol/ui` capability) returned tools with `_meta.ui.resourceUri`
   in both forms, the resource list, and a 308KB `text/html;profile=mcp-app` read.
2. The built iframe bundle contains `ui/initialize`, `ui/notifications/initialized`
   and `ui/notifications/tool-result`, so it is spec-compliant.
3. Claude Desktop chat advertises `io.modelcontextprotocol/ui`. Claude Code Desktop
   (CCD) does not, per the prior session's RPC log, and renders no iframes.
4. The sibling server log showed zero `tools/call` or `resources/read` since ~20:35Z,
   so there was no ground truth yet that the iframe renders in Claude Desktop chat.
5. Amplitude MCP has no image export. `render_chart` returns CSV plus a chart-edit URL
   on amplitude.com, which rules out the markdown image embed (Path C).

## Decision: Path D

Bypass MCP. The chart data was already in the session from `render_chart`, so:

1. Write a self-contained HTML file with the data inlined.
2. Make a one-character `Edit` to trigger CCD's `PostToolUse:Edit` hook, which opens
   the Launch preview panel.

This is the sibling's Launch preview pattern without a new server.

## Data in hand

"Export: Error Rate (%)", last 60 days (2026-02-19 to 2026-04-20), 60 points each for
formula A (trendline, 0.24% to 0.28%) and formula B (raw, 0.12-0.94%, spikes on
2026-03-26 at 0.56 and 2026-03-27 at 0.94).

## Next step

Write `~/projects/amplitude-error-rate-chart.html` with an inline SVG or canvas chart,
then trigger Launch preview. Success shows MCP Apps is not required for inline visuals in CCD.
