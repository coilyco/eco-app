import { formatCount, formatMoney, prettifyEcoName } from "../lib/format"
import ChartFrame from "./ChartFrame"
import ItemPrice from "./ItemPrice"
import { useSvgTextScale } from "../hooks/useSvgTextScale"
import type { ItemPriceHistory, PriceHistoryState } from "../lib/priceHistoryApi"

const STATE_TEXT: Record<PriceHistoryState, string> = {
  no_data: "No unit-price observations exist for this item and currency in the current cycle.",
  thin: "Thin sample: treat the distribution and price range as individual observations, not a stable market pattern.",
  stale: "Stale sample: this item's latest trade predates newer current-cycle market evidence.",
  multimodal: "Multiple price clusters are visible. One median would hide distinct trading regimes, so the histogram stays discrete.",
  missing_recipes: "No known recipe produces this item, so required-specialty markers cannot be resolved.",
  missing_progression: "The GainSpecialty progression export is unavailable, so recipe requirements are known but unlock timing is not.",
  unobserved_unlocks: "At least one required specialty has no observed current-cycle gain. That is not evidence that the specialty was never available.",
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
    return <p className="empty-note">No price timeline can be drawn without priced trades.</p>
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
      above={[`high ${formatMoney(maxPrice)}`, `low ${formatMoney(minPrice)}`, "bars show volume"]}
      start={`Day ${minDay}`}
      end={`Day ${maxDay}`}
    >
    <svg
      ref={svgRef}
      className="price-history-chart"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`Current-cycle ${history.itemPretty} median price, range, volume, and required specialty unlocks`}
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
    return <p className="empty-note">No observed prices fall inside this current-cycle window.</p>
  }
  const maxCount = Math.max(...distribution.histogram.map((bucket) => bucket.count), 1)
  return (
    <>
      <div
        className="price-histogram"
        role="img"
        aria-label={`Histogram of ${distribution.sampleCount} observed unit prices`}
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
            <span className="rank-name">Sample and freshness</span>
            <span className="rank-count">
              {formatCount(distribution.sampleCount)} trades · {distribution.sampleState} ·{" "}
              {distribution.freshnessState}
            </span>
          </div>
        </li>
        <li>
          <div className="rank-row">
            <span className="rank-name">Median and range</span>
            <span className="rank-count">
              <ItemPrice
                price={distribution.median}
                norm={history.norm}
                currency={history.currency}
                showCurrency
                suffix={`, range ${formatMoney(distribution.min!)}–${formatMoney(distribution.max!)}`}
              />
            </span>
          </div>
        </li>
        {distribution.percentiles && (
          <li>
            <div className="rank-row">
              <span className="rank-name">Percentiles</span>
              <span className="rank-count">
                p10 {formatMoney(distribution.percentiles.p10)}, p25{" "}
                {formatMoney(distribution.percentiles.p25)}, p75{" "}
                {formatMoney(distribution.percentiles.p75)}, p90{" "}
                {formatMoney(distribution.percentiles.p90)}
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
        Current-cycle price history{" "}
        <span className="section-sub">(distribution + production capability)</span>
      </h2>
      <p className="empty-note" data-testid="price-history-scope">
        {history.scope.label}. Older cycles are excluded because their star and progression rules
        may not be comparable ({history.scope.progressionRulesVersion}).
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
          <h3 className="subsection-title">Observed unit-price distribution</h3>
          <Distribution history={history} />
        </div>
        <div>
          <h3 className="subsection-title">Price, volume, and specialty arrivals</h3>
          <PriceTimeline history={history} />
          {history.specialtyUnlocks.length === 0 ? (
            <p className="empty-note" data-testid="price-unlocks-empty">
              No required specialties can be resolved from the known recipe graph.
            </p>
          ) : (
            <ul className="rank-rows" data-testid="price-unlocks">
              {history.specialtyUnlocks.map((marker) => (
                <li key={marker.skill}>
                  <div className="rank-row">
                    <span className="rank-name">{marker.skillPretty}</span>
                    <span className="rank-count">
                      {marker.status === "observed"
                        ? `first observed day ${marker.day}`
                        : marker.status === "unobserved"
                          ? "no observed current-cycle gain"
                          : "progression export unavailable"}
                    </span>
                  </div>
                  <p className="section-sub">
                    Required by {marker.recipeVariants.map(prettifyEcoName).join(", ")}
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
