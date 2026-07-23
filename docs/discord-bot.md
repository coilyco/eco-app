# Discord rich slash-command embeds

Implementation specification for [eco-app#143](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/143).

## Outcome

Eco Discord slash commands always return one branded rich embed. Command handlers never return a successful plain-text message. The bot reads the existing eco-app JSON data planes and links each response back to the relevant SPA page.

The first release provides read-only discovery. It does not parse ordinary messages, replace website unfurls, mutate the Eco server, or send unsolicited notifications.

## User experience

Discord registers one top-level `/eco` command group:

* `/eco status` - current server state, online-player count, meteor countdown, world age, and version. The embed links to `/info`.
* `/eco world` - climate and world summary with the current world-preview image when Discord can fetch it. The embed links to `/map`.
* `/eco economy` - current currency, trade, and supply-gap highlights. The embed links to `/trade`.
* `/eco player <name>` - the named citizen's public dossier summary. The embed links to the existing encoded player URL.
* `/eco help` - a compact command directory. The embed links to the eco-app homepage.

The implementation may add commands after the first release only when an existing public eco-app data plane can supply the answer. The bot does not reach around eco-app to call the game server directly.

Every public command response contains exactly one embed and no `content` string. Error, empty, permission, timeout, and degraded responses also use the shared embed shape. The bot makes error responses ephemeral when Discord still permits that choice at initial acknowledgement. Successful responses are visible in the channel.

## Shared embed contract

One embed factory owns the presentation contract. Command handlers supply typed content to the factory and do not construct raw Discord embeds themselves.

Every embed contains:

* A stable Eco brand color.
* A title that names the command result.
* A canonical eco-app HTTPS URL on the title.
* The Eco application icon as the author or thumbnail image.
* A short description that remains useful when optional fields are absent.
* At most 25 fields, with the most important facts first.
* A UTC timestamp representing the data fetch time.
* A footer that identifies the Eco server and says `Live data from eco-app`.

The factory provides `success`, `degraded`, `empty`, and `error` variants. The degraded variant names unavailable sections without exposing internal hosts, exception strings, stack traces, tokens, or opaque identifiers.

The renderer enforces Discord's current message limits before network delivery:

* One embed per response.
* Title at most 256 characters.
* Description at most 4,096 characters.
* Field name at most 256 characters.
* Field value at most 1,024 characters.
* At most 25 fields.
* Total embed text at most 6,000 characters.

The renderer truncates on semantic boundaries and appends a link to the full SPA page. The renderer never lets Discord reject a response because upstream Eco text is too long.

## Interaction lifecycle

Every handler acknowledges the interaction before performing network or disk work. The handler defers immediately, then fetches eco-app data, renders the embed, and edits the deferred response.

The bot applies a shorter internal request timeout than Discord's deferred-interaction lifetime. A timed-out or failed eco-app request produces the shared error embed. The bot never leaves a deferred interaction unresolved.

The bot uses slash commands only. It requests no Message Content, Guild Members, or Presence privileged intents. The bot uses Discord's default non-privileged intents unless a later feature has a documented need for more.

Command registration runs as an explicit deployment operation, not during every worker startup. The deploy workflow registers guild-scoped commands in a test server first. The operator promotes the same command schema to global registration after validation.

## Application architecture

The eco-app repo owns:

* A new Python package for the Discord worker, command handlers, typed response models, eco-app HTTP client, and embed factory.
* The command schema and explicit registration entrypoint.
* Unit tests and contract tests.
* The Discord library dependency and its lockfile entry.
* Public-safe documentation and operator-facing environment-variable names.

The worker consumes eco-app over its public or cluster-local HTTP interface. The worker reuses the existing `/preview*.json` endpoints instead of importing MCP handler internals. This boundary keeps the bot deployable as an independent process and exercises the same data contract as the SPA.

The worker runs as a separate process from Uvicorn. The published eco-app image contains both entrypoints, while the deploy repo creates a dedicated Discord worker Deployment from that image. A gateway reconnect or bot crash cannot take down the web and MCP service.

The first implementation uses Pycord with async HTTP calls. The worker keeps Discord gateway callbacks non-blocking and lets the library handle Discord rate-limit responses.

## Configuration and ownership

The deploy repo owns the Discord worker Deployment, rollout, environment wiring, and ExternalSecret. The eco-app repo does not contain Kubernetes manifests or live Discord identifiers.

The worker accepts:

* `ECO_DISCORD_TOKEN` - required secret bot token.
* `ECO_DISCORD_APPLICATION_ID` - required Discord application identifier.
* `ECO_DISCORD_TEST_GUILD_ID` - optional identifier used only by the guild-scoped registration operation.
* `ECO_DISCORD_ECO_APP_URL` - eco-app base URL. The deploy supplies the cluster-local service URL, while local development may use localhost.
* `ECO_DISCORD_PUBLIC_URL` - canonical public SPA base URL used in embed links.
* `ECO_DISCORD_SERVER_LABEL` - public-safe server name shown in footers.

AWS SSM owns the opaque Discord token and identifiers. The deploy repo maps those parameters into the worker through an ExternalSecret. Tracked files use meaningful environment-variable names and test-only placeholders, never live values.

The Discord application invite requests only the `bot` and `applications.commands` OAuth scopes. The initial permission set allows the bot to view the selected channel, send messages, and embed links. The operator does not grant Administrator.

## Reliability and observability

The HTTP client sets bounded connect and response timeouts and reports partial upstream availability to the renderer. Commands degrade per section when a response can still provide useful data.

The worker emits structured logs for startup, gateway readiness, command name, outcome class, total duration, upstream duration, and Discord request correlation. Logs exclude command option values that can identify a player unless the existing public product already exposes that value.

The worker initializes the repo's existing Sentry integration when `SENTRY_DSN` is present. The worker records unexpected failures after redaction and still resolves the interaction with an error embed.

The deployment uses one worker replica initially. The implementation does not add sharding until Discord scale requires it. The worker handles termination signals and closes the Discord and HTTP clients cleanly so a rollout does not strand sockets.

## Tests

Application tests cover:

* Every first-release command maps to the intended eco-app endpoint and canonical SPA URL.
* Every success path returns exactly one embed and no plain-text content.
* Empty, degraded, timeout, malformed-upstream, and unexpected-error paths return the appropriate rich embed.
* The embed factory enforces every Discord length and field-count limit.
* Arbitrarily long and markup-heavy Eco values cannot create invalid mentions or rejected embeds.
* Every handler defers before the mocked upstream request begins.
* The HTTP client redacts secrets and internal connection details from user-facing errors.
* Command registration is an explicit entrypoint and worker startup does not register commands.
* The bot requests no privileged intents.

Tests use mocked Discord interactions and HTTP responses. The test suite makes no Discord API calls and needs no token.

The deploy change follows the established eco-app service pattern. A director or ops run performs live Discord registration and gateway verification because those checks require externally visible Discord state.

## Acceptance criteria

The implementation is complete when:

* `ward exec test`, `ward exec lint`, `ward exec smoke`, and `ward exec precommit` pass in eco-app.
* The eco-app image contains documented web-server, Discord-worker, and command-registration entrypoints.
* The deploy repo contains the separate worker Deployment and ExternalSecret wiring.
* A guild-scoped test registration exposes all five first-release commands.
* Each command produces exactly one branded embed with no successful plain-text fallback.
* A deliberately unavailable eco-app upstream produces a resolved rich error embed rather than `The application did not respond`.
* The bot operates without privileged gateway intents or Administrator permission.
* A director or ops run records live verification evidence on the implementation issue before global command registration.

## Out of scope

The first release excludes:

* Automatic handling of URLs pasted into ordinary messages.
* Open Graph website metadata.
* Prefix commands and message parsing.
* Buttons, selects, modals, pagination, and autocomplete.
* Eco server mutations, moderation, direct messages, scheduled posts, and trade-watch notifications.
* Multiple guild-specific configurations.
* Discord sharding.

These exclusions keep the first implementation limited to deterministic, read-only rich responses over data eco-app already publishes.
