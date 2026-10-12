# Plot twist: which client is this, actually?

2026-04-20 ~15:18 PT. Full original: `git show b012073:investigation/1776724300_02_client_identity_plot_twist.md`.

## The finding

In session `c952c964` Kai said she was in **Claude Code Desktop**, not Claude Desktop
chat, and that the Launch preview panel is what rendered HTML. Three clients are in play:

- **Claude Desktop (chat UI)**, `clientInfo.name` `claude-ai`: renders MCP Apps iframes
  and advertises `extensions.io.modelcontextprotocol/ui`.
- **Claude Code CLI**, `claude-code`: no iframes, text output only.
- **Claude Code Desktop**, `local-agent-mode-<server>`: no iframes, but a Launch
  preview panel opens HTML files when Claude applies an Edit to them.

## What "worked a week ago" probably was

The sibling MCP App's Launch preview fallback, not an iframe. `server.ts:358-367`
writes `projects.html` on every tool call, and a `PostToolUse:Edit` hook opens it
when Claude applies a trivial Edit, as the tool descriptions instruct. Amplitude's
`render_chart` has no such fallback, so Kai saw its plain-text JSON.

## Paths

1. **Claude Desktop chat renders the Amplitude iframe.** Needs the user to switch
   client, the Amplitude server connected there (no server log under
   `~/Library/Logs/Claude/`), `_meta.ui.resourceUri` emitted, and a full handshake.
   Unlikely, given Kai's explicit Claude Code Desktop context.
2. **Launch preview renders the chart.** Wrap the chart in an HTML file, write it to
   disk, trigger an Edit. Needs a proxy MCP server like the sibling. The brute-force
   path Kai was probably asking for.
3. **Claude Code Desktop supports MCP Apps.** It does not advertise the UI extension,
   and that is a client build Anthropic controls.

## Next step

1. Check whether Amplitude is connected in Claude Desktop chat (unlikely).
2. Read session `f3a122e0` for the earlier rendering tests.
3. Read `c952c964` fully for what was already tried.
4. Check `~/Library/Logs/Claude/` for Amplitude server logs.
