# Investigation - MCP Apps in Claude Desktop

A chronological log of an ~8-hour debug session on how Claude Desktop's MCP Apps spec
behaves in practice. Written against a private sibling MCP App (TypeScript, React,
`@modelcontextprotocol/ext-apps`), then copied here and scrubbed of the sibling's product
specifics when `eco-mcp-app` was built on the same foundation. The findings are unchanged.
The sibling's tRPC endpoints, product terms, tokens, env var names, coworker names and CDN
URLs became placeholders (`sibling-mcp-app`, `SIBLING_API_TOKEN`, `cdn.example.com`).

## How to read them

Start with [`1776724900_04_master_findings.md`](1776724900_04_master_findings.md), the
consolidated view. For the narrative, read in numeric order:

- **00_bootstrap** - goal, ground rules, starting artifacts.
- **01_github_issues_digest** - the `anthropics/claude-ai-mcp` issues that matter (#40,
  #61, #69, #71, #102, #142, #149, #165).
- **02_client_identity_plot_twist** - Claude Desktop chat and Claude Code Desktop are
  separate surfaces with different MCP capabilities.
- **03_prior_session_summary** - what an earlier session established about CCD's gaps.
- **04_master_findings** - the consolidated state, read this if resuming.
- **05_pivot_to_action** and **06_path_d_worked** - dropping iframe-in-CCD for a Launch
  preview path, and it worked.
- **07_actual_fix** - the real bug, a hardcoded `img-src` CSP (#40), and the data-URI
  workaround.
- **08_eco_mcp_app_debug_chain** - what a second MCP App in Python surfaced: the
  `ui/initialize` param shape, `size-changed`, the SDK return type, the dev harness.
- **issue_61_reply_draft** - a ready-to-post comment for anthropics/claude-ai-mcp#61.
- **gh_issue_audience_filter** - a draft issue for the empty widget after
  `audience=["user"]`.

Files 00-08 and the audience-filter draft were condensed to the documentation caps. The
full originals are in git history at `b012073`.
