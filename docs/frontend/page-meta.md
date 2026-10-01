# Page titles, descriptions and OpenGraph tags

Every route can carry a `title` and a `description` in `data/spa_routes.json`,
next to `path`, `component` and `crawl`. The manifest is the one place both the
frontend and the service read, so the words live there and nowhere else.

## What reads them

* **The frontend, today.** `frontend/src/components/PageMeta.tsx` applies the
  route's words on every navigation: the tab title, `description`, `og:title`,
  `og:description` and `og:url`. This is what Google sees once it runs the
  bundle, and what the tab shows. `frontend/src/lib/pageMeta.ts` holds the logic.
* **The service, not yet.** Link-preview scrapers such as Slack and Discord
  generally do not run JavaScript, so they read the shell as served, which is the
  same for every route. Rewriting the shell's `<head>` per route is
  teable:coilyco/eco-app#8576, and it reads the same two fields.

## Rules the code keeps

* **A route with no words gets the shell's.** The shell's title and description
  are captured once before the first route writes anything, and a route without
  its own puts them back, so leaving a titled page never leaves its title behind.
* **`og:url` is the canonical or nothing.** It was the home page on every page,
  which contradicted the canonical `Link` header. Now it matches that header for
  an indexed route, and the tag is removed for a `noindex` route, a URL with a
  query string, and a path deeper than a wildcard route such as `/jobs/professions`.
  That mirrors `seo.classify`.
* **Both or neither, at search-result length.** `routes.test.tsx` fails a route
  with only one of the two, a title over 60 characters, or a description over 160.
  Completeness is deliberately not asserted, so a route can wait for its words.

## Not done

* The words for most routes: teable:coilyco/eco-app#8577.
* An `og:image`. Nothing in eco-app is a link-preview card, so previews are text
  only. One default 1200x630 card would be a start.
