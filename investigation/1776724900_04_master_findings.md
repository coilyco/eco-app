# Master findings - consolidated (read this one if resuming)

2026-04-20 ~15:28 PT. Original: `git show b012073:investigation/1776724900_04_master_findings.md`.

## The single most important fact
Claude Code Desktop (CCD), the client Kai was using, **does not advertise
`extensions.io.modelcontextprotocol/ui`**. Confirmed in session `c952c964` by teeing
init traffic via `MCP_RPC_LOG`: capabilities were only `{"roots":{"listChanged":true}}`.
So no MCP Apps iframe renders in CCD whatever the server sends. Claude Desktop chat
(`claude-ai`) does advertise it, but Amplitude MCP was configured only in CCD.

## What "worked a week ago" was
A Launch preview fallback, not an iframe. Each tool call regenerates an HTML file, the
response tells Claude to apply a trivial Edit, and CCD's `PostToolUse:Edit` hook opens
the file in a side panel. Amplitude's `render_chart` has no such fallback: it returns
CSV data, a `shouldRenderUI: true` flag (Amplitude-proprietary, not MCP Apps spec) and a
`chartEditUrl` pointing at amplitude.com.

## GitHub correlation
- [#165](https://github.com/anthropics/claude-ai-mcp/issues/165): closest, but its stack
  is Claude Desktop chat, not CCD.
- [#61](https://github.com/anthropics/claude-ai-mcp/issues/61#issuecomment-4283640203):
  three causes (missing handshake, DNS block, HTTP session split).
- No issue documents CCD lacking MCP Apps. Possibly worth filing.

## Three viable paths
- **A, test in Claude Desktop chat.** Add Amplitude MCP to
  `claude_desktop_config.json`, restart, ask for a chart. Risks: Amplitude's OAuth may
  only work in CCD, and `render_chart` probably emits no `_meta.ui.resourceUri`.
  Check its `tools/list` for `_meta.ui.*`.
- **B, CCD-native renderer.** A wrapper tool queries Amplitude, writes a Chart.js HTML
  file, and asks Claude to Edit it. Works in CCD, costs a wrapper per chart.
- **C, image embed.** If Amplitude can export a PNG or SVG, embed it as a markdown
  image. Unchecked: whether it can, and whether CCD renders markdown images.

## Next step (if resuming)
1. Look for image export or a renderable response in the Amplitude tool surface.
2. If none, build Path B, probably what Kai wants.
3. If still none, file "CCD doesn't support MCP Apps" against anthropics/claude-ai-mcp.
If `MCP_RPC_LOG` is enabled again via `claude_desktop_config.json`, remove it afterwards.
