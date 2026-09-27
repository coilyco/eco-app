import { describe, expect, it } from "vitest"

import { compareToUsual, describeNorm, formatMultiple, type PriceNorm } from "./priceNorm"

const base: PriceNorm = {
  stage: "Basic 4",
  basis: "stage",
  fallback: null,
  n: 12,
  cycles: 5,
  referencePrice: 0.84,
  referenceCurrency: "Spectres",
  referenceCurrencyId: "2533707",
  multiple: 1.3,
  median: 0.7,
  p25: 0.6,
  p75: 0.8,
  currency: "Spectres",
  cycle: 14,
  caveat: "stage trails the tech level",
}

describe("describeNorm", () => {
  it("says so when the server sent no norm", () => {
    expect(describeNorm(undefined).state).toBe("missing")
    expect(describeNorm(null).short).toBe("no norm sent")
  })

  it("says there is no history when the basis is null", () => {
    const t = describeNorm({ ...base, basis: null, n: 0, cycles: 0, multiple: null })
    expect(t.state).toBe("none")
    expect(t.short).toBe("no past sales yet")
  })

  it("gives the stage comparison as a percent and always the sale count", () => {
    const t = describeNorm(base, "Spectres")
    expect(t.state).toBe("ok")
    expect(t.short).toBe("30% over the usual Basic 4 price (12 sales)")
    expect(t.full).toBe("This is 30% over the usual Basic 4 price, based on 12 sales over 5 cycles.")
    expect(describeNorm({ ...base, multiple: 0.6 }).short).toBe("40% under the usual Basic 4 price (12 sales)")
  })

  it("names the all-stage fallback and why", () => {
    const t = describeNorm({ ...base, basis: "all", fallback: "only 3 trades at Basic 4 across cycles, using all stages", n: 134 })
    expect(t.state).toBe("fallback")
    expect(t.short).toBe("30% over the usual price across all stages (134 sales)")
    expect(t.full).toContain("across all upgrade stages, based on 134 sales over 5 cycles.")
    expect(t.full).toContain("Only 3 trades at Basic 4 across cycles, using all stages.")
  })

  it("says the currency is not identified for a bare ledger id, not that there is no history", () => {
    const t = describeNorm({ ...base, multiple: null, median: null }, "2533707")
    expect(t.state).toBe("unidentified")
    expect(t.short).toBe("can't compare currencies (12 sales)")
    expect(t.full).toContain("The usual Basic 4 price is 0.84 Spectres.")
    expect(t.full).not.toMatch(/no history|no past sales/i)
  })

  it("falls back to the in-currency median from the most recent cycle", () => {
    const t = describeNorm({ ...base, multiple: null }, "Barter")
    expect(t.state).toBe("median")
    expect(t.short).toBe("usually 0.7 Spectres (12 sales)")
    expect(t.full).toContain("In cycle 14 the middle price was 0.7 Spectres.")
  })

  it("gives the usual live-currency price when a named currency has no history", () => {
    const t = describeNorm({ ...base, multiple: null, median: null, currency: null }, "Barter")
    expect(t.state).toBe("elsewhere")
    expect(t.short).toBe("usually 0.84 Spectres (12 sales)")
    expect(t.full).toContain("No past sales of this item in Barter.")
  })

  it("says the currency is not known, never no history, when the page has none", () => {
    const t = describeNorm({ ...base, multiple: null, median: null }, undefined)
    expect(t.state).toBe("reference")
    expect(t.short).toBe("usually 0.84 Spectres (12 sales)")
    expect(t.full).not.toMatch(/no past (trades|sales)|no history/i)
  })

  it("never uses a middle dot", () => {
    for (const cur of ["Spectres", "2533707", "Barter"]) {
      const t = describeNorm({ ...base, multiple: null }, cur)
      expect(t.short + t.full).not.toContain("·")
    }
  })
})

describe("compareToUsual", () => {
  const usual = "the usual Modern 4 price"
  it("reads within 5% as about the usual price", () => {
    expect(compareToUsual(1, usual)).toBe("about the usual Modern 4 price")
    expect(compareToUsual(1.04, usual)).toBe("about the usual Modern 4 price")
    expect(compareToUsual(0.96, usual)).toBe("about the usual Modern 4 price")
    expect(compareToUsual(1.05, usual)).toBe("5% over the usual Modern 4 price")
    expect(compareToUsual(0.95, usual)).toBe("5% under the usual Modern 4 price")
  })

  it("gives percent under 2x and times from 2x", () => {
    expect(compareToUsual(0.6, usual)).toBe("40% under the usual Modern 4 price")
    expect(compareToUsual(1.99, usual)).toBe("99% over the usual Modern 4 price")
    expect(compareToUsual(2, usual)).toBe("2 times the usual Modern 4 price")
    expect(compareToUsual(2.54, usual)).toBe("2.5 times the usual Modern 4 price")
    expect(compareToUsual(12.6, usual)).toBe("13 times the usual Modern 4 price")
  })

  it("never says a priced item is 100% under", () => {
    expect(compareToUsual(0.001, usual)).toBe("99% under the usual Modern 4 price")
    expect(compareToUsual(0, usual)).toBe("100% under the usual Modern 4 price")
  })
})

describe("formatMultiple", () => {
  it("keeps one decimal under 10 and rounds above", () => {
    expect(formatMultiple(1.34)).toBe("1.3x")
    expect(formatMultiple(12.6)).toBe("13x")
    expect(formatMultiple(0.04)).toBe("<0.1x")
  })
})
