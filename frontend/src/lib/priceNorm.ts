// The historical price norm every price-bearing payload object carries
// (eco-app#8368). The backend computes it, including `multiple`, so the browser
// does no basket math. Method: docs/price-history.md. Rendering: docs/frontend/price-norms.md.

// Shared once per payload as `normContext` (stage, currency, caveat).
export interface NormContext {
  stage?: string | null
  cycle?: number | null
  referenceCurrency?: string | null
  referenceCurrencyId?: string | null
  caveat?: string | null
  source?: string | null
  notes?: string[]
}

// Per object the server omits null keys except basis and n. hydrateNorms folds
// the payload's normContext in, so a norm read on a page carries both.
export interface PriceNorm extends Omit<NormContext, "cycle"> {
  basis: "stage" | "all" | null
  n: number
  fallback?: string | null
  cycles?: number | null
  referencePrice?: number | null
  multiple?: number | null
  median?: number | null
  p25?: number | null
  p75?: number | null
  currency?: string | null
  currencyN?: number | null
  /** The cycle the in-currency median comes from. */
  cycle?: number | null
  /** The live cycle, from normContext.cycle. */
  liveCycle?: number | null
}

// Walks a payload once and merges its normContext into every `norm` it holds.
export function hydrateNorms<T>(payload: T): T {
  const raw = (payload as { normContext?: NormContext } | null)?.normContext
  if (!raw || typeof raw !== "object") return payload
  // normContext.cycle is the live cycle; a norm's own `cycle` is its median's.
  const { cycle: liveCycle, ...rest } = raw
  const ctx = { ...rest, liveCycle }
  const seen = new Set<object>()
  const walk = (node: unknown): void => {
    if (!node || typeof node !== "object" || seen.has(node)) return
    seen.add(node)
    if (Array.isArray(node)) {
      node.forEach(walk)
      return
    }
    const obj = node as Record<string, unknown>
    for (const [key, value] of Object.entries(obj)) {
      if (key === "norm" && value && typeof value === "object") obj.norm = { ...ctx, ...(value as object) }
      else if (key !== "normContext") walk(value)
    }
  }
  walk(payload)
  return payload
}

export type NormState = "missing" | "none" | "unidentified" | "median" | "reference" | "elsewhere" | "fallback" | "ok"

export interface NormText {
  state: NormState
  short: string
  full: string
}

// Ledger payloads still carry bare currency ids until eco-app#217 resolves them.
function isBareCurrencyId(currency: string | null | undefined): boolean {
  return !!currency && /^\d+$/.test(currency.trim())
}

export function formatMultiple(m: number): string {
  if (m < 0.1) return "<0.1x"
  return `${m < 10 ? m.toFixed(1) : Math.round(m)}x`
}

function money(v: number): string {
  return v.toLocaleString("en-US", { maximumFractionDigits: v < 10 ? 2 : 0 })
}

function trades(n: number, cycles: number): string {
  const t = `${n.toLocaleString("en-US")} trade${n === 1 ? "" : "s"}`
  return cycles > 1 ? `${t} over ${cycles} cycles` : cycles === 1 ? `${t} in 1 cycle` : t
}

function stageName(norm: PriceNorm): string {
  return norm.basis === "stage" && norm.stage ? `${norm.stage} ` : ""
}

function reference(norm: PriceNorm): string {
  if (norm.referencePrice == null) return ""
  const cur = norm.referenceCurrency ?? (norm.referenceCurrencyId ? `currency ${norm.referenceCurrencyId}` : "")
  return `The usual ${stageName(norm)}price is ${money(norm.referencePrice)}${cur ? ` ${cur}` : ""}. `
}

function sameCurrency(norm: PriceNorm, currency: string | null | undefined): boolean {
  if (!currency) return false
  const c = currency.trim()
  return c === norm.referenceCurrency || c === norm.referenceCurrencyId
}

// The page divides its own price by referencePrice, because one object can carry
// two prices (buy and sell, cheapest and median) and the server's `multiple`
// covers only one. It falls back to the server's figure when currencies differ.
export function multipleFor(norm: PriceNorm, price: number | null | undefined, currency?: string | null): number | null {
  if (price != null && norm.referencePrice && sameCurrency(norm, currency)) return price / norm.referencePrice
  return norm.multiple ?? null
}

// `currency` is the currency of the price this norm sits beside, as the page has it.
export function describeNorm(
  norm: PriceNorm | null | undefined,
  currency?: string | null,
  price?: number | null,
): NormText {
  if (!norm) {
    return { state: "missing", short: "no norm sent", full: "The server sent no price norm with this price." }
  }
  const count = trades(norm.n, norm.cycles ?? 0)
  if (norm.basis === null || norm.n === 0) {
    return { state: "none", short: "no history yet", full: "No trades of this item in past cycles yet." }
  }
  const fallback = norm.fallback ? ` ${norm.fallback.charAt(0).toUpperCase()}${norm.fallback.slice(1)}.` : ""
  const multiple = multipleFor(norm, price, currency)
  if (multiple !== null) {
    const m = formatMultiple(multiple)
    if (norm.basis === "all" || norm.fallback) {
      return {
        state: "fallback",
        short: `${m} usual (all stages), ${norm.n.toLocaleString("en-US")} trades`,
        full: `${m} the usual price across all upgrade stages, from ${count}.${fallback}`,
      }
    }
    return {
      state: "ok",
      short: `${m} usual${norm.stage ? ` ${norm.stage}` : ""}, ${norm.n.toLocaleString("en-US")} trades`,
      full: `${m} the usual ${stageName(norm)}price, from ${count}.`,
    }
  }
  if (isBareCurrencyId(currency)) {
    return {
      state: "unidentified",
      short: `currency not identified, ${norm.n.toLocaleString("en-US")} trades`,
      full: `${reference(norm)}This price's currency (${currency}) is not identified yet, so there is no multiple. From ${count}.`,
    }
  }
  if (norm.median != null) {
    const cur = norm.currency ? ` ${norm.currency}` : ""
    return {
      state: "median",
      short: `usual ${money(norm.median)}${cur}, ${norm.n.toLocaleString("en-US")} trades`,
      full: `Usually ${money(norm.median)}${cur} in cycle ${norm.cycle ?? "?"}. ${reference(norm)}From ${count}.${fallback}`,
    }
  }
  // No multiple is possible here, so the usual price in the live currency is the comparison.
  const usual =
    norm.referencePrice != null
      ? `usual ${money(norm.referencePrice)}${norm.referenceCurrency ? ` ${norm.referenceCurrency}` : ""}, `
      : ""
  const n = `${norm.n.toLocaleString("en-US")} trades`
  if (!currency?.trim()) {
    return {
      state: "reference",
      short: `${usual}${n}`,
      full: `${reference(norm)}This price's currency is not known here, so there is no multiple. From ${count}.`,
    }
  }
  return {
    state: "elsewhere",
    short: usual ? `${usual}${n}` : `no history in ${currency}, ${n} in others`,
    full: `No past trades of this item in ${currency}. ${reference(norm)}From ${count}.`,
  }
}
