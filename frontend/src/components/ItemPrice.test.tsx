import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import type { PriceNorm } from "../lib/priceNorm"
import ItemPrice from "./ItemPrice"

afterEach(() => cleanup())

const NORM: PriceNorm = {
  basis: "stage",
  n: 134,
  cycles: 11,
  referencePrice: 0.8,
  stage: "Modern 4",
  referenceCurrency: "Spectres",
  referenceCurrencyId: null,
}

describe("ItemPrice", () => {
  it("divides its own price by the reference when the currency matches", () => {
    render(<ItemPrice price={1.2} norm={NORM} currency="Spectres" showCurrency data-testid="p" />)
    const el = screen.getByTestId("p")
    expect(el).toHaveTextContent("1.2 Spectres")
    expect(el).toHaveTextContent("1.5x usual Modern 4, 134 trades")
    expect(el.querySelector(".k-sr-only")).toHaveTextContent("1.5x the usual Modern 4 price, from 134 trades over 11 cycles.")
    expect(el.querySelector("[data-norm-state]")).toHaveClass("item-price__norm--far")
  })

  it("keeps a sentence intact inline", () => {
    render(<ItemPrice price={0.8} norm={NORM} currency="Spectres" prefix="@ " layout="inline" data-testid="p" />)
    expect(screen.getByTestId("p")).toHaveTextContent("@ 0.8 (1.0x usual Modern 4, 134 trades)")
  })

  it("shows nothing for a missing norm but still marks it for the check", () => {
    render(<ItemPrice price={2} norm={undefined} data-testid="p" />)
    const el = screen.getByTestId("p")
    expect(el).toHaveTextContent(/^2$/)
    expect(el.querySelector("[data-norm-state]")).toHaveAttribute("data-norm-state", "missing")
  })

  it("renders a dash and its suffix with no norm when there is no price", () => {
    render(<ItemPrice price={null} norm={NORM} suffix=", 4 in stock" data-testid="p" />)
    const el = screen.getByTestId("p")
    expect(el).toHaveTextContent("—, 4 in stock")
    expect(el.querySelector("[data-price]")).toBeNull()
  })
})
