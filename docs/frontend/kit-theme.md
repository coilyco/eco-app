# The kit theme

eco-app takes its visual system from the coilyco kit, the design system in
`coilyco/website` under `packages/kit`, rather than from a token block of its
own. Moved over in `teable:coilyco/eco-app#8308`.

## How it is wired

* `frontend/src/vendor/coilyco-kit/` - the compiled kit CSS and its three
  woff2 faces, copied verbatim from `packages/kit/dist/coilyco-kit.css` and
  `packages/kit/vendor/fonts`. Never edit them here. To refresh, copy the files
  again and note the website commit in `eco-theme.css`. A shipped app never
  fetches its styling from the website at runtime.
* `frontend/src/eco-theme.css` - imports the kit, declares the faces, and
  swaps kit **primitives only**. Every semantic token the kit derives from them
  (`--k-ground`, `--k-brand`, `--k-edge`, the shadows) follows on its own.
* `frontend/src/index.css` - eco-app's own class names. Its token block is now
  a set of aliases onto kit semantic tokens (`--leaf` is `--k-brand`, `--card`
  is `--k-surface`), so no rule below it holds a colour. Discord's blurple on
  the Discord button is the one literal left, because it is Discord's brand.
* `<body class="k">` opts the page into the kit root: type, line height, and
  the kit's `:focus-visible` ring. `Layout.tsx` wraps every page in `.k-page`.

## Where the colours come from

All of them come from the game's own globe icon,
`src/eco_mcp_app/assets/eco-icon.png`.

* **Ground and structure** - the kit's ink ladder, same OKLCH lightness and
  chroma per step, rotated to hue 142, the icon's foliage green (`#588d53`).
  It reads as forest floor at night rather than as grey.
* **Brand** - the same hue at the kit's brand lightness steps, chroma capped at
  0.14 so it stays a leaf rather than a highlighter. `--k-b-400` is `#60ac59`,
  4.89:1 on the lightest green surface.
* **Accent** - the icon's ocean, hue 235, `#45aade`. The kit draws every edge
  in the accent, so the edges read as coastline.
* **Meteor amber** - `--eco-meteor`, `#f0b35c`, eco-app's own primitive and
  deliberately **not** the kit accent. Were it the accent, every edge on the
  site would be amber and the countdown would stop being the one warning.
* **Registers** (`cost`, `refuse`, `grant`, `context`, `reference`, `dim`) -
  re-derived until each clears 4.5:1 on the lightest green surface. `grant`
  moved to teal, since at the kit's hue it sat 8 degrees from the brand.
* **Eco rich text** - the game's named colours `green` and `blue` (the server
  name is `<color=green>Eco</color> via <color=blue>Sirens</color>`) and the
  dark ones map onto theme tokens in `EcoRichText.tsx`. Player-picked hex values
  render as written.

## The chrome

The layout is the kit's page shell, the same one coilysiren.me uses, so the
flair comes from the kit rather than from eco-app CSS.

* `.k-page` paints the kit's planet texture on a fixed layer behind every
  page. It is built from `--k-brand` and `--k-accent`, so it renders as a green
  planet with ocean light, and it is the site's own look, approved by Kai.
* `k-nav` and `k-footer` are the frame bands with the accent edge. Below 600px
  the kit swaps the nav brand for the `k-nav-stub` header.
* The Eco globe (`frontend/src/assets/eco-icon.png`) stands in for the kit's
  brandmark, which is Kai's personal mark.
* Directory cards on `/` are `a.k-card`, so they carry the neon hover.

## Checked

Against the running SPA at 1280px, 390px, and 320px, on live data and with the
API forced to fail: no horizontal overflow, no text pair under 4.5:1 (3:1 for
large text) on `/`, `/info`, `/trade`, or `/jobs`, and a visible focus ring
under keyboard.

## Not done yet

Type below the kit's 16px floor survives on most pages, and components still
use eco-app classes rather than `k-` ones. Both move page by page.
