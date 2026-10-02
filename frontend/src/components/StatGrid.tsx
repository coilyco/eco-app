import type { EcoStatus } from "../lib/api"
import { formatCount } from "../lib/format"

interface Stat {
  label: string
  value: string
  detail?: string
}

export default function StatGrid({ status }: { status: EcoStatus }) {
  const stats: Stat[] = [
    {
      label: "Online now",
      value: formatCount(status.players.online),
      detail: `${formatCount(status.players.activeAndOnline)} active this week`,
    },
    {
      label: "Settlers",
      value: formatCount(status.players.total),
      detail: `peak ${formatCount(status.players.peakActive)} at once`,
    },
    { label: "Plants growing", value: formatCount(status.world.plants) },
    { label: "Laws in force", value: formatCount(status.world.laws) },
    {
      label: "Total culture",
      value: formatCount(status.world.totalCulture),
      // The server's own counter is unreliable per server; when it reads 0
      // against real milestone progress we show the milestone floor and say so
      // (eco-app#237).
      detail:
        status.world.totalCultureSource === "milestones"
          ? "At least this much, counted from milestones. The server's own count says 0."
          : undefined,
    },
    {
      label: "Economy",
      value: status.economy.description.split(",")[0] ?? status.economy.description,
      detail: status.economy.description.split(",").slice(1).join(",").trim() || undefined,
    },
    { label: "World", value: status.world.size, detail: status.cycle.gameSpeed.toLowerCase() + " game speed" },
    { label: "Server", value: status.server.version.split(" ")[0] ?? "", detail: status.server.category },
  ]

  return (
    <section aria-label="world totals">
      <div className="k-facts info-facts" data-testid="world-facts">
        {stats.map((s) => (
          <div className="k-fact" key={s.label}>
            <span className="k-fact__label">{s.label}</span>
            <span className="k-fact__value">{s.value}</span>
            {s.detail && <span className="info-fact-detail">{s.detail}</span>}
          </div>
        ))}
      </div>
    </section>
  )
}
