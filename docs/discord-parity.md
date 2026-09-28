# DiscordLink preview parity

Inventory captured from `eco-ops/mods/Configs/DiscordLink.eco` on 2026-07-23.
Only read-only display/preview surfaces are in scope. The configured duplex
chat bridge stays enabled, and automatic notification feeds are not command previews.

Each active DiscordLink surface, with its slash-command replacement, the eco-app data plane behind it, the SPA link, and its state:

* **Server information display** (players, in-game time, meteor, elections)
  * Replacement - `/eco status` and `/eco player <name>`.
  * Data plane - `/preview.json`, `/preview/user.json`.
  * SPA link - `/`, `/civics`.
  * State - Implemented. Election detail is on the civics page rather than an always-posted channel card. The per-player web page was removed (teable:coilyco/eco-app#8385), so player detail is Discord-only.
* **Map display**
  * Replacement - `/eco world`.
  * Data plane - `/preview/world.json`.
  * SPA link - `/map`.
  * State - Implemented.
* **Work-party display**
  * Replacement - none safe yet.
  * Data plane - no public work-party data plane exists.
  * SPA link - none.
  * State - Director/ops checkpoint: add or approve a public-safe eco-app work-party plane before disabling this DiscordLink display.

The active trade, crafting, server-status, player-status, and election feed
channels are automatic notifications rather than read-only previews. They stay
outside this slash-command replacement. Chat sync remains explicitly out of
scope and must remain enabled.

Before promotion, an operator registers the schema in the test guild with
`eco-discord-register`, checks all five `/eco` commands against the live
service, and records the result. Do not globally register commands or disable
any DiscordLink display until that checkpoint is complete.
