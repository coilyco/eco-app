import { useMemo, useState } from "react"
import type { PollutersBlock } from "../lib/climateApi"
import { formatCount, formatDecimal } from "../lib/format"
import {
  ALL_OWNERS,
  filterByOwner,
  ownerOptions,
  type PolluterSortKey,
  sortPolluters,
  type SortDirection,
  totalPolluters,
} from "../lib/polluters"

const COLUMNS: { key: PolluterSortKey; label: string; num?: boolean }[] = [
  { key: "name", label: "Device" },
  { key: "owner", label: "Owner" },
  { key: "rate", label: "Pollution", num: true },
]

// Every air-polluting device in the world with its owner and how much it makes,
// from the telemetry mod (COI-3149). The totals row follows the owner filter, so
// filtering to one owner answers "how much does this person pollute".
export default function PollutersTable({ block }: { block: PollutersBlock }) {
  const [owner, setOwner] = useState(ALL_OWNERS)
  const [sortKey, setSortKey] = useState<PolluterSortKey>("rate")
  const [direction, setDirection] = useState<SortDirection>("desc")

  const options = useMemo(() => ownerOptions(block.devices), [block.devices])
  const shown = useMemo(
    () => sortPolluters(filterByOwner(block.devices, owner), sortKey, direction),
    [block.devices, owner, sortKey, direction],
  )
  const totals = useMemo(() => totalPolluters(shown), [shown])
  const unit = block.rate_unit ?? "tons/hour"

  if (!block.available) {
    return (
      <section data-testid="polluters">
        <h3 className="section-title">Polluting devices</h3>
        <p className="gap-who" data-testid="polluters-unavailable">
          The list of polluting devices is not available right now. This server is not reporting it yet.
        </p>
      </section>
    )
  }

  if (block.devices.length === 0) {
    return (
      <section data-testid="polluters">
        <h3 className="section-title">Polluting devices</h3>
        <p className="gap-who" data-testid="polluters-empty">No polluting devices are built in the world right now.</p>
      </section>
    )
  }

  const choose = (key: PolluterSortKey) => {
    if (key === sortKey) {
      setDirection(direction === "asc" ? "desc" : "asc")
    } else {
      setSortKey(key)
      // A rate reads best biggest first, and names read best A to Z.
      setDirection(key === "rate" ? "desc" : "asc")
    }
  }

  return (
    <section data-testid="polluters">
      <h3 className="section-title">Polluting devices</h3>
      <p className="intro">
        <span>
          Every device that puts pollution into the air, who owns it, and how much it makes. The
          amount is what the device is set up to make each hour, so a device that is switched off
          still shows its amount. The Running column says which ones are on right now.
        </span>
      </p>
      <div className="filter-row">
        <select
          className="filter-select"
          value={owner}
          onChange={(e) => setOwner(e.target.value)}
          aria-label="Filter by owner"
          data-testid="polluters-owner-filter"
        >
          <option value={ALL_OWNERS}>All owners</option>
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>
      <table tabIndex={0} className="ledger-table" data-testid="polluters-table">
        <thead>
          <tr>
            {COLUMNS.map((c) => (
              <th
                key={c.key}
                className={c.num ? "num" : undefined}
                aria-sort={c.key === sortKey ? (direction === "asc" ? "ascending" : "descending") : "none"}
              >
                <button
                  type="button"
                  className="linklike"
                  onClick={() => choose(c.key)}
                  data-testid={`polluters-sort-${c.key}`}
                >
                  {c.label}
                  {c.key === sortKey ? (direction === "asc" ? " ▲" : " ▼") : ""}
                </button>
                {c.key === "rate" ? ` (${unit})` : ""}
              </th>
            ))}
            <th>Position</th>
            <th>Running</th>
            <th className="num">Made so far (tons)</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((d, i) => (
            <tr key={d.object_id ?? `${d.type}-${i}`} data-testid="polluters-row">
              <td>{d.name}</td>
              <td>{d.owner ?? "No owner"}</td>
              <td className="num" data-testid="polluters-rate">
                {d.rate_per_hour === null ? "Unknown" : formatDecimal(d.rate_per_hour)}
              </td>
              <td>{d.position ? `${d.position.x}, ${d.position.y}, ${d.position.z}` : "Unknown"}</td>
              <td>{d.operating === null ? "Unknown" : d.operating ? "Yes" : "No"}</td>
              <td className="num">{d.total_tons === null ? "Unknown" : formatDecimal(d.total_tons)}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr data-testid="polluters-total">
            <th scope="row">
              Total, {formatCount(totals.devices)} device{totals.devices === 1 ? "" : "s"}
            </th>
            <td />
            <td className="num" data-testid="polluters-total-rate">{formatDecimal(totals.rate)}</td>
            <td />
            <td data-testid="polluters-total-running">
              {formatCount(totals.running)} running, making {formatDecimal(totals.runningRate)} {unit}
            </td>
            <td />
          </tr>
        </tfoot>
      </table>
      {totals.unknownRate > 0 && (
        <p className="gap-who" data-testid="polluters-unknown-rate">
          {formatCount(totals.unknownRate)} device{totals.unknownRate === 1 ? "" : "s"} in this list
          {totals.unknownRate === 1 ? " has" : " have"} no readable amount and {totals.unknownRate === 1 ? "is" : "are"} left
          out of the totals.
        </p>
      )}
    </section>
  )
}
