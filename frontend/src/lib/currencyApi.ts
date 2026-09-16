// Typed client for the money-supply snapshot (/preview/currency.json).
//
// The endpoint returns CurrencySnapshot.to_dict() from eco_mcp_app/currency.py:
// the money supply summarised — per-currency records (minted amount, mint
// events, trade count & volume, creator) plus optional admin-only time series
// (active currencies, personal wealth, government holdings). `adminOk` is false
// when the admin token is absent, in which case the series are empty and only
// the public per-currency headline survives. Resolves to null on a missing /
// failing endpoint so the /trade currency strip degrades on its own
// (eco-app#54).

import { fetchJsonOrNull } from "./api"

export interface CurrencyHolder {
  account: string
  holder: string | null
  balance: number
}

export interface CurrencyRecord {
  name: string
  isMinted: boolean
  mintedAmount: number
  mintEvents: number
  tradeCount: number
  tradeVolume: number
  createdBy: string | null
  // Live per-account balances from the stores/economy exporter mod (eco-app#58).
  // Optional: present in the snapshot payload, not yet rendered by the currency
  // strip. `holdersReachable` is false when that mod is not deployed on the server.
  holdersReachable?: boolean
  // null when holdersReachable is false: unread, not zero. eco-app#6077.
  totalHoldings?: number | null
  accountsCounted?: number | null
  topHolders?: CurrencyHolder[]
}

export interface CurrencySnapshot {
  fetchedAtISO: string
  sourceBaseUrl: string
  daysElapsed: number
  adminOk: boolean
  // [time, value]
  activeCurrenciesSeries: Array<[number, number]>
  personalWealthSeries: Array<[number, number]>
  governmentHoldingsSeries: Array<[number, number]>
  currencies: CurrencyRecord[]
  tradeRowsTotal: number
  tradeCurrencyColumnSeen: boolean
  availableCurrencyDatasets: string[]
  warnings: string[]
}

export async function fetchCurrency(signal?: AbortSignal): Promise<CurrencySnapshot | null> {
  // limit=0: the SPA renders the full set; the bounded default exists for
  // MCP callers, who have a response cap the browser does not (eco-app#256).
  const body = await fetchJsonOrNull<CurrencySnapshot>(
    "/preview/currency.json?limit=0",
    signal,
  )
  if (!body || !Array.isArray(body.currencies)) return null
  return body
}
