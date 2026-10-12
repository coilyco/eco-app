# GitHub issues digest - anthropics/claude-ai-mcp

Saved 2026-04-20 ~15:12 PT. Original: `git show b012073:investigation/1776723900_01_github_issues_digest.md`.

## Closest match: #165 (open)
Iframe handshake never starts after a tool call. Reporter: ext-apps 1.1.2, stdio,
macOS. `tools/call` and `resources/read` succeed, then silence.

## The key comment: #61, @ochafik, 2026-04-20
Three causes that look identical:

- **A, missing handshake.** The host keeps the container `visibility: hidden` until
  the View sends `ui/initialize`, gets the response, then sends
  `ui/notifications/initialized`. Fix: bundle `@modelcontextprotocol/ext-apps` and
  call `app.connect()`. A no-script HTML test cannot rule this out.
- **B, blocked wildcard.** `claudemcpcontent.com` has no apex A record by design, only
  `*.claudemcpcontent.com` resolves. The "not blocked" popup means DNS blocks it.
- **C, two sessions over HTTP.** One session for the model, one for AppBridge, each
  with its own `Mcp-Session-Id`. Per-session resource registration makes
  `resources/read ui://...` miss. Register UI resources statelessly (ext-apps#481).

@shickle's report (ext-apps 1.6.0, stdio, Apple Silicon) matches Kai's stack most
closely: clean initialize, then no `ui/notifications/*` and an empty iframe.

## Other issues saved

- #47: claude.ai injects non-JSON-RPC `{type, token, payload}` into postMessage.
- #69: `size-changed` ignored, so set the root element height.
- #71: only flat `_meta["ui/resourceUri"]` allegedly works. The sibling sets both.
- #102: `ui/update-model-context` dropped on claude.ai.
- #40: claude.ai ignores `_meta.ui.csp.*`, hardcodes `frame-src 'self' blob: data:`.
- #142: feature request for `mcp://ui/` citation links.
- #149 (closed): do not call tools during init, set `ontoolresult` before `connect()`.
  MCP Apps push results, they do not pull.

## Takeaways and next step

Test with the real app. "Worked a week ago" is suspect: check whether the tool emitted
`_meta.ui.resourceUri` or only Amplitude's custom `shouldRenderUI`, and whether Kai
was in Claude Desktop chat or Claude Code Desktop.
