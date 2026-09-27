import { formatCount, formatMoney, prettifyEcoName } from "../lib/format"
import ChartFrame from "./ChartFrame"
import ItemPrice from "./ItemPrice"
import { useSvgTextScale } from "../hooks/useSvgTextScale"
import type { ItemPriceHistory, PriceHistoryState } from "../lib/priceHistoryApi"

const STATE_TEXT: Record<PriceHistoryState, string> = {
  no_data: "No sales of this item in this currency yet this cycle.",
  thin: "Only a few sales so far. Read the prices below as single sales, not a steady market price.",
  stale: "Out of date: this item has not sold for a while, but other trading has carried on since.",
  multimodal: "Sales bunch up at a few different prices. One middle price would hide that, so the chart keeps each group separate.",
  missing_recipes: "No known recipe makes this item, so there are no specialties to mark.",
  missing_progression: "The record of when players learned specialties is not available, so the recipes' specialties are known but not when players got them.",
  unobserved_unlocks: "Nobody has been seen learning at least one needed specialty this cycle. That does not prove nobody has it.",
}

// The server sends these as codes. The words are shown after the trade count.
const SAMPLE_TEXT: Record<string, string> = {
  no_data: "none yet",
  thin: "only a few",
  representative: "enough to go on",
}
const FRESHNESS_TEXT: Record<string, string> = {
  current: "recent",
  stale: "out of date",
  unknown: "age unknown",
}

function PriceTimeline({ history }: { history: ItemPriceHistory }) {
  const svgRef = useSvgTextScale<SVGSVGElement>()
  const observedMarkers = history.specialtyUnlocks.filter(
    (marker) => marker.status === "observed" && marker.day !== null,
  )
  const days = [
    ...history.daily.map((bucket) => bucket.day),
    ...observedMarkers.map((marker) => marker.day as number),
  ]
  if (history.daily.length === 0) {
    return <p className="empty-note">No priced sales yet, so there is no price chart.</p>
  }

  const width = 720
  const height = 260
  const left = 48
  const right = 18
  const top = 40
  const priceBottom = 184
  const volumeTop = 202
  const volumeBottom = 230
  const minDay = Math.min(...days)
  const maxDay = Math.max(...days)
  const daySpan = maxDay - minDay || 1
  const prices = history.daily.flatMap((bucket) => [bucket.min, bucket.max])
  const minPrice = Math.min(...prices)
  const maxPrice = Math.max(...prices)
  const priceSpan = maxPrice - minPrice || 1
  const maxVolume = Math.max(...history.daily.map((bucket) => bucket.volume), 1)
  const x = (day: number) => left + ((day - minDay) / daySpan) * (width - left - right)
  const y = (price: number) => priceBottom - ((price - minPrice) / priceSpan) * (priceBottom - top)
  const line = history.daily
    .map((bucket) => `${x(bucket.day).toFixed(1)},${y(bucket.median).toFixed(1)}`)
    .join(" ")
  const volumeWidth = Math.max(3, (width - left - right) / Math.max(history.daily.length * 2, 1))

  return (
    <ChartFrame
      above={[`high ${formatMoney(maxPrice)}`, `low ${formatMoney(minPrice)}`, "bars show how many sold each day"]}
      start={`Day ${minDay}`}
      end={`Day ${maxDay}`}
    >
    <svg
      ref={svgRef}
      className="price-history-chart"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`${history.itemPretty} prices this cycle: middle price, range, amount sold, and when needed specialties were learned`}
      data-testid="price-history-chart"
    >
      {history.daily.map((bucket) => (
        <g key={`day-${bucket.day}`}>
          <line
            className="price-range-line"
            x1={x(bucket.day)}
            x2={x(bucket.day)}
            y1={y(bucket.max)}
            y2={y(bucket.min)}
          />
          <rect
            className="price-volume-bar"
            x={x(bucket.day) - volumeWidth / 2}
            y={volumeBottom - (bucket.volume / maxVolume) * (volumeBottom - volumeTop)}
            width={volumeWidth}
            height={(bucket.volume / maxVolume) * (volumeBottom - volumeTop)}
          />
        </g>
      ))}
      <polyline className="price-median-line" points={line} />
      {history.daily.map((bucket) => (
        <circle
          key={`median-${bucket.day}`}
          className="price-median-point"
          cx={x(bucket.day)}
          cy={y(bucket.median)}
          r="3"
        />
      ))}
      {observedMarkers.map((marker, index) => (
        <g key={marker.skill} className="specialty-marker" data-testid="specialty-marker">
          <line x1={x(marker.day!)} x2={x(marker.day!)} y1={top - 8} y2={priceBottom} />
          <circle cx={x(marker.day!)} cy={top - 8} r="4" />
          <text x={x(marker.day!)} y={14 + (index % 2) * 14} textAnchor="middle">
            {marker.skillPretty}
          </text>
        </g>
      ))}
    </svg>
    </ChartFrame>
  )
}

function Distribution({ history }: { history: ItemPriceHistory }) {
  const distribution = history.distribution
  if (distribution.sampleCount === 0) {
    return <p className="empty-note">No sale prices yet this cycle.</p>
  }
  const maxCount = Math.max(...distribution.histogram.map((bucket) => bucket.count), 1)
  return (
    <>
      <div
        className="price-histogram"
        role="img"
        aria-label={`Chart of ${distribution.sampleCount} sale prices, grouped by price`}
        data-testid="price-histogram"
      >
        {distribution.histogram.map((bucket, index) => (
          <div className="price-histogram-bin" key={`${bucket.low}-${bucket.high}-${index}`}>
            <span className="price-histogram-count">{formatCount(bucket.count)}</span>
            <span
              className="price-histogram-bar"
              style={{ height: `${Math.max(6, (bucket.count / maxCount) * 100)}%` }}
            />
            <span className="price-histogram-range">
              {formatMoney(bucket.low)}–{formatMoney(bucket.high)}
            </span>
          </div>
        ))}
      </div>
      <ul className="rank-rows" data-testid="price-distribution-evidence">
        <li>
          <div className="rank-row">
            <span className="rank-name">How many trades, and how recent</span>
            <span className="rank-count">
              {formatCount(distribution.sampleCount)} trades,{" "}
              {SAMPLE_TEXT[distribution.sampleState] ?? distribution.sampleState},{" "}
              {FRESHNESS_TEXT[distribution.freshnessState] ?? distribution.freshnessState}
            </span>
          </div>
        </li>
        <li>
          <div className="rank-row">
            <span className="rank-name">Middle price and range</span>
            <span className="rank-count">
              <ItemPrice
                price={distribution.median}
                norm={history.norm}
                currency={history.currency}
                showCurrency
                suffix={`, range ${formatMoney(distribution.min!)} to ${formatMoney(distribution.max!)}`}
              />
            </span>
          </div>
        </li>
        {distribution.percentiles && (
          <li>
            <div className="rank-row">
              <span className="rank-name">Cheap and pricey ends</span>
              <span className="rank-count">
                10% sold for {formatMoney(distribution.percentiles.p10)} or less, 25% for{" "}
                {formatMoney(distribution.percentiles.p25)} or less, 25% for{" "}
                {formatMoney(distribution.percentiles.p75)} or more, 10% for{" "}
                {formatMoney(distribution.percentiles.p90)} or more
              </span>
            </div>
          </li>
        )}
      </ul>
    </>
  )
}

export default function PriceHistoryPanel({ history }: { history: ItemPriceHistory }) {
  return (
    <section data-testid="price-history">
      <h2 className="section-title">
        Price history this cycle{" "}
        <span className="section-sub">(what it sold for, and when players could craft it)</span>
      </h2>
      <p className="empty-note" data-testid="price-history-scope">
        {history.scope.label}. Older cycles are excluded because their star and skill rules
        may be different ({history.scope.progressionRulesVersion}).
      </p>
      {history.states.length > 0 && (
        <ul className="warn-list price-history-states" data-testid="price-history-states">
          {history.states.map((state) => (
            <li key={state}>{STATE_TEXT[state]}</li>
          ))}
        </ul>
      )}

      <div className="atlas-columns price-history-columns">
        <div>
          <h3 className="subsection-title">How many sold at each price</h3>
          <Distribution history={history} />
        </div>
        <div>
          <h3 className="subsection-title">Price by day, amount sold, and specialties learned</h3>
          <PriceTimeline history={history} />
          {history.specialtyUnlocks.length === 0 ? (
            <p className="empty-note" data-testid="price-unlocks-empty">
              No needed specialties found in the known recipes.
            </p>
          ) : (
            <ul className="rank-rows" data-testid="price-unlocks">
              {history.specialtyUnlocks.map((marker) => (
                <li key={marker.skill}>
                  <div className="rank-row">
                    <span className="rank-name">{marker.skillPretty}</span>
                    <span className="rank-count">
                      {marker.status === "observed"
                        ? `first learned on day ${marker.day}`
                        : marker.status === "unobserved"
                          ? "nobody seen learning it this cycle"
                          : "learning record not available"}
                    </span>
                  </div>
                  <p className="section-sub">
                    Needed for {marker.recipeVariants.map(prettifyEcoName).join(", ")}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {history.warnings.length > 0 && (
        <ul className="warn-list" data-testid="price-history-warnings">
          {history.warnings.map((warning) => (
            <li key={warning}>⚠ {warning}</li>
          ))}
        </ul>
      )}
    </section>
  )
}
