import { formatPrice, UNREPORTED } from "../lib/format"
import { describeNorm, multipleFor, type PriceNorm } from "../lib/priceNorm"

interface Props {
  price: number | null | undefined
  /** Required, so a price rendered without its norm fails the type check. */
  norm: PriceNorm | null | undefined
  /** The price's currency as the page has it, name or bare ledger id. */
  currency?: string | null
  /** Print the currency after the number. */
  showCurrency?: boolean
  /** Words before the number, such as "@ " or "from ". */
  prefix?: string
  /** Words after the number and currency, such as "/unit". */
  suffix?: string
  /** "stack" puts the norm on its own line (cells, tiles). "inline" and "clause"
   * add it after a comma, since the note carries its own "(N sales)". */
  layout?: "stack" | "inline" | "clause"
  /** The short norm with the full sentence for assistive tech and hover. */
  compact?: boolean
  "data-testid"?: string
}

// Every item price on eco-app renders through this, so the historical norm sits
// beside it in one form everywhere (eco-app#8368). docs/frontend/price-norms.md.
export default function ItemPrice({
  price,
  norm,
  currency,
  showCurrency = false,
  prefix = "",
  suffix = "",
  layout = "stack",
  compact = true,
  "data-testid": testId,
}: Props) {
  // No price means nothing to compare, so no norm, but the suffix (a quantity) stays.
  if (price == null || !Number.isFinite(price)) return <span data-testid={testId}>{`${UNREPORTED}${suffix}`}</span>
  const text = describeNorm(norm, currency, price)
  const m = norm ? multipleFor(norm, price, currency) : null
  // Judged on the multiple rounded to one decimal, so 1.5x (50% over) or 0.7x
  // (30% under) always reads as far.
  const shown = m === null ? null : Math.round(m * 10) / 10
  const far = shown !== null && (shown >= 1.5 || shown <= 0.7)
  const value = `${prefix}${formatPrice(price)}${showCurrency && currency ? ` ${currency}` : ""}${suffix}`
  // A missing norm is a wiring gap for the check to catch, not news for a player.
  const note =
    text.state === "missing" ? null : compact ? (
      <>
        <span aria-hidden="true">{text.short}</span>
        <span className="k-sr-only">{text.full}</span>
      </>
    ) : (
      text.full
    )
  return (
    <span className={`item-price item-price--${layout}`} data-price="" data-testid={testId}>
      <span className="item-price__value">{value}</span>
      {(layout === "inline" || layout === "clause") && note ? ", " : null}
      <span
        className={`item-price__norm item-price__norm--${text.state}${far ? " item-price__norm--far" : ""}`}
        data-norm-state={text.state}
        title={compact && note ? text.full : undefined}
      >
        {note}
      </span>
    </span>
  )
}
