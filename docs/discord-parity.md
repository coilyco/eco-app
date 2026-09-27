# DiscordLink rich-preview parity

Inventory captured from `eco-ops/mods/Configs/DiscordLink.eco` on 2026-07-23.
Only read-only display/preview surfaces are in scope; the configured duplex
chat bridge stays enabled and automatic notification feeds are not command previews.

| Active DiscordLink surface | Rich Eco replacement | eco-app data plane | SPA link | State |
| --- | --- | --- | --- | --- |
| Server information display (players, in-game time, meteor, elections) | `/eco status` and `/eco player <name>` | `/preview.json`, `/preview/user.json` | `/`, `/civics` | Implemented; election detail is on the civics page rather than an always-posted channel card. The per-player web page was removed (eco-app#8385), so player detail is Discord-only. |
| Map display | `/eco world` | `/preview/world.json` | `/map` | Implemented. |
| Work-party display | No safe replacement yet | No public work-party data plane exists | — | Director/ops checkpoint: add or approve a public-safe eco-app work-party plane before disabling this DiscordLink display. |

The active trade, crafting, server-status, player-status, and election feed
channels are automatic notifications rather than read-only previews. They stay
outside this slash-command replacement. Chat sync remains explicitly out of
scope and must remain enabled.

Before promotion, an operator registers the schema in the test guild with
`eco-discord-register`, checks all five `/eco` commands against the live
service, and records the result. Do not globally register commands or disable
any DiscordLink display until that checkpoint is complete.
