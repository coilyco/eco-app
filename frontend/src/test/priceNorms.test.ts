/// <reference types="node" />
import { readdirSync, readFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { describe, expect, it } from "vitest"

// The static half of eco-app#8368's acceptance check: an item price can only
// reach the page through components/ItemPrice, which always carries its norm.
// The render half, against real annotated payloads, is priceNormsRender.test.tsx.

const SRC = path.join(fileURLToPath(import.meta.url), "../..")
const sources = (dir: string) =>
  readdirSync(path.join(SRC, dir))
    .filter((f) => f.endsWith(".tsx") && !f.endsWith(".test.tsx"))
    .map((f) => ({ rel: `${dir}/${f}`, text: readFileSync(path.join(SRC, dir, f), "utf8") }))
const FILES = [...sources("pages"), ...sources("components")]

// Loading any of these means the file holds item prices.
const PRICE_DATA =
  /\b(fetchLogistics|fetchMarket|fetchItemPivot|fetchItemPriceHistory|fetchStores|fetchWatchers|fetchRecipeIndexWithCost)\b|recipes\.json\?cost=1/

// Files that load price data but show no item unit price, each with the reason.
const NO_ITEM_PRICE: Record<string, string> = {
  "pages/Jobs.tsx": "shows margins and value-board totals, never a unit price",
}

describe("price norms, static", () => {
  it("formats an item price only inside ItemPrice", () => {
    const offenders = FILES.filter((f) => f.rel !== "components/ItemPrice.tsx" && /\bformatPrice\(/.test(f.text))
    expect(offenders.map((f) => f.rel)).toEqual([])
  })

  it("defines no local fractional money formatter that could sidestep it", () => {
    const offenders = FILES.filter((f) => /maximumFractionDigits/.test(f.text))
    expect(offenders.map((f) => f.rel)).toEqual([])
  })

  it("renders ItemPrice in every file that loads price data", () => {
    const missing = FILES.filter(
      (f) => PRICE_DATA.test(f.text) && !(f.rel in NO_ITEM_PRICE) && !/<ItemPrice\b/.test(f.text),
    )
    expect(missing.map((f) => f.rel)).toEqual([])
  })

  it("keeps the allowlist honest", () => {
    for (const rel of Object.keys(NO_ITEM_PRICE)) {
      const file = FILES.find((f) => f.rel === rel)
      expect(file, `${rel} is allowlisted but missing`).toBeDefined()
      expect(/<ItemPrice\b/.test(file!.text), `${rel} renders ItemPrice, drop it from NO_ITEM_PRICE`).toBe(false)
    }
  })

  it("catches a price page that skips ItemPrice (negative control)", () => {
    const fake = { rel: "pages/Fake.tsx", text: 'import { fetchMarket } from "../lib/marketApi"\n{formatMoney(row.medianPrice)}' }
    expect(PRICE_DATA.test(fake.text) && !/<ItemPrice\b/.test(fake.text)).toBe(true)
  })
})
