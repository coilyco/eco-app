# The kit theme

eco-app takes its visual system from the coilyco kit (`coilyco/website`, under `packages/kit`) rather than a token block of its own (`teable:coilyco/eco-app#8308`).

## How it is wired

* `frontend/src/vendor/coilyco-kit/` - the compiled kit CSS and three woff2 faces, copied verbatim from `packages/kit/dist/coilyco-kit.css` and `packages/kit/vendor/fonts`. Never edit here. To refresh, copy again and note the website commit in `eco-theme.css`. A shipped app never fetches styling from the website at runtime.
* `frontend/src/eco-theme.css` - imports the kit, declares the faces, swaps kit **primitives only**. Every semantic token (`--k-ground`, `--k-brand`, `--k-edge`, the shadows) follows.
* `frontend/src/index.css` - eco-app's own classes. Its token block aliases kit semantic tokens (`--leaf` is `--k-brand`, `--card` is `--k-surface`), so no rule below holds a colour. The one literal left is Discord's blurple on the Discord button.
* `<body class="k">` opts into the kit root (type, line height, `:focus-visible` ring). `Layout.tsx` wraps every page in `.k-page`.

## Where the colours come from

All from the game's globe icon, `src/eco_mcp_app/assets/eco-icon.png`.

* **Ground and structure** - the kit's ink ladder, same OKLCH lightness and chroma, rotated to hue 142, the icon's foliage green (`#588d53`).
* **Brand** - the same hue at the kit's brand lightness steps, chroma capped at 0.14. `--k-b-400` is `#60ac59`, 4.89:1 on the lightest green surface.
* **Accent** - the icon's ocean, hue 235, `#45aade`. The kit draws every edge in the accent.
* **Meteor amber** - `--eco-meteor`, `#f0b35c`, eco-app's own primitive and deliberately **not** the kit accent, or every edge would be amber and the countdown would stop being the one warning.
* **Registers** (`cost`, `refuse`, `grant`, `context`, `reference`, `dim`) - re-derived to clear 4.5:1 on the lightest green surface. `grant` moved to teal, 8 degrees from the brand at the kit's hue.
* **Eco rich text** - the game's named colours `green`, `blue` and the dark ones map onto theme tokens in `EcoRichText.tsx`. Player-picked hex renders as written.

## The chrome

* `.k-page` paints the kit's planet texture on a fixed layer, built from `--k-brand` and `--k-accent` (a green planet with ocean light, approved by Kai).
* `k-nav` and `k-footer` are the frame bands with the accent edge. Below 600px the kit swaps the nav brand for the `k-nav-stub` header.
* The Eco globe (`frontend/src/assets/eco-icon.png`) stands in for the kit's brandmark, which is Kai's personal mark.
* Directory cards on `/` are `a.k-card`, with the neon hover.

The kit's CI checks run on eco-app too: [kit-checks.md](kit-checks.md).

Not done: type below the 16px floor survives on most pages, and components still use eco-app classes rather than `k-` ones. Both move page by page.
