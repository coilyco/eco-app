import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { MemoryRouter } from "react-router-dom"
import Home from "./Home"
import { SAMPLE_STATUS } from "../test/fixtures"
import { SERVER_BRIEF } from "../lib/serverBrief"

function renderHome() {
  return render(
    <MemoryRouter>
      <Home />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe("Home", () => {
  it("renders the directory with live badges", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(SAMPLE_STATUS), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    )

    renderHome()

    expect(
      screen.getByText(/Tools for playing Eco on this server/i),
    ).toHaveTextContent("what happened while you were away")
    // /info folded into this page (eco-app#8385), so it has no card.
    expect(screen.queryByTestId("dir-info")).not.toBeInTheDocument()
    expect(screen.getByTestId("dir-jobs")).toHaveAttribute("href", "/jobs")
    expect(screen.getByTestId("dir-wiki")).toHaveAttribute("href", "/wiki")
    // The eco-gnome calculator is a homepage card that links out to the gnome
    // service, not a /calculator route anymore (eco-app#90).
    expect(screen.getByTestId("dir-gnome")).toHaveAttribute(
      "href",
      "https://eco-gnome.coilysiren.me/",
    )
    // /economy is gone entirely (eco-app#90) — no economy card. The standalone
    // trades and climate cards folded into Trade and World respectively.
    expect(screen.queryByTestId("dir-economy")).not.toBeInTheDocument()
    expect(screen.queryByTestId("dir-trades")).not.toBeInTheDocument()
    expect(screen.queryByTestId("dir-climate")).not.toBeInTheDocument()

    // The world totals that lived on /info.
    await waitFor(() => {
      expect(screen.getByTestId("world-facts")).toHaveTextContent("Online now")
    })
    expect(screen.getByRole("link", { name: "Join the Discord" })).toHaveAttribute(
      "href",
      "https://discord.gg/example",
    )
  })

  it("keeps each fact once: the totals repeat nothing the facts row holds, and make no claim about a week", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(SAMPLE_STATUS), { status: 200, headers: { "Content-Type": "application/json" } }),
      ),
    )
    renderHome()
    const totals = (await screen.findByTestId("world-facts")).textContent ?? ""
    expect(totals).not.toMatch(/game speed/i)
    expect(totals).not.toMatch(/active this week/i)
    expect(totals).toContain("Server type")
    expect(totals).toContain(SAMPLE_STATUS.server.category)
  })

  it("names every region differently, so a screen reader can tell them apart", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(SAMPLE_STATUS), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    )

    renderHome()
    await waitFor(() => {
      expect(screen.getByTestId("world-facts")).toBeInTheDocument()
    })

    // axe landmark-unique: the world totals sit inside the "world at a glance"
    // region and used to carry the same name (eco-app#8584).
    const names = screen.getAllByRole("region").map((region) => region.getAttribute("aria-label"))
    const labelled = names.filter((name): name is string => Boolean(name))
    expect(new Set(labelled).size).toBe(labelled.length)
    expect(labelled).toContain("world at a glance")
    expect(labelled).toContain("world totals")
  })

  it("renders per-surface sub-card badges from the live pulse endpoints", async () => {
    const byUrl: Record<string, unknown> = {
      "/preview.json": SAMPLE_STATUS,
      "/preview/get_trades.json": {
        totalTrades: 1341,
        totalCurrencyVolume: 4907,
        byItem: [["BunWulfRawMeatItem", 90, 400]],
      },
      "/preview/get_crafting_atlas.json": {
        totalEvents: 512,
        byCrafted: [["WoodenChairItem", 40]],
      },
      "/preview/get_climate.json": {
        status: "warming",
        co2: { current: 620 },
      },
      "/preview/get_region.json": {
        biomes: [{ display: "Grassland" }],
        ecoregionMatches: [{ name: "Serengeti" }],
      },
      "/preview/world.json": {
        totalEvents: 421,
        categories: [{ key: "construction", label: "Construction", events: 300, volume: 1 }],
      },
    }

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input.toString()
        const key = Object.keys(byUrl).find((u) => url.includes(u))
        const body = key ? byUrl[key] : {}
        return Promise.resolve(
          new Response(JSON.stringify(body), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        )
      }),
    )

    renderHome()

    // Trade + trades ledger are one card now (eco-app#90): volume and the trade
    // count come from the ledger's totalCurrencyVolume / totalTrades, and
    // markets falls back to a graceful 0 when the market plane is empty.
    await waitFor(() => {
      expect(screen.getByTestId("trade-badges")).toHaveTextContent("1,341 trades")
    })
    expect(screen.getByTestId("trade-badges")).toHaveTextContent("4,907 spent")
    expect(screen.getByTestId("trade-badges")).toHaveTextContent("0 priced items")
    expect(screen.getByTestId("trade-badges")).toHaveTextContent("top seller: Bun Wulf Raw Meat")
    expect(screen.getByTestId("crafting-badges")).toHaveTextContent("512 crafts")
    expect(screen.getByTestId("crafting-badges")).toHaveTextContent("top: Wooden Chair")
    // World + ecoregion merged into the one /map card (eco-app#82) and climate
    // folded in (eco-app#90): its badge strip carries world events, the busiest
    // category, the dominant biome, and the climate headline + CO₂.
    expect(screen.getByTestId("world-badges")).toHaveTextContent("421 events")
    expect(screen.getByTestId("world-badges")).toHaveTextContent("Construction")
    expect(screen.getByTestId("world-badges")).toHaveTextContent("Grassland")
    expect(screen.getByTestId("world-badges")).toHaveTextContent("warming")
    expect(screen.getByTestId("world-badges")).toHaveTextContent("CO₂ 620 parts per million")
  })

  it("renders the full directory even when the snapshot fetch fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("boom")))

    renderHome()

    await waitFor(() => {
      expect(screen.getByTestId("dir-jobs")).toBeInTheDocument()
    })
    expect(screen.queryByTestId("world-facts")).not.toBeInTheDocument()
  })

  it("keeps the brief, the join steps, and the invite on screen when live status fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("boom")))

    renderHome()

    await waitFor(() => {
      expect(screen.getByTestId("home-live")).toHaveTextContent("Can't reach the server right now")
    })
    expect(screen.getByRole("heading", { level: 1, name: "Eco via Sirens" })).toBeInTheDocument()
    // The reviewed copy carries the invite, so the CTA survives an outage.
    expect(screen.getByRole("link", { name: "Join the Discord" })).toHaveAttribute(
      "href",
      SERVER_BRIEF.join.discordUrl,
    )
    // Per-cycle facts never fall back to a stale copy. With no live status the
    // whole strip steps aside rather than showing a row of Unknowns.
    expect(screen.queryByTestId("home-facts")).not.toBeInTheDocument()
    expect(screen.getByTestId("home-live")).not.toHaveTextContent(/cycle \d/i)
    expect(screen.queryByTestId("home-meteor")).not.toBeInTheDocument()
    expect(screen.getByTestId("home-configs")).toHaveTextContent(SERVER_BRIEF.configs[0].group)
  })

  it("reads cycle, world size, and a destroyed meteor from live status", async () => {
    const live = {
      ...SAMPLE_STATUS,
      server: {
        ...SAMPLE_STATUS.server,
        description: "Eco via Sirens | Cycle 14 | High Collab | 100 x 100",
        detailedDescription: "Cycle 14. 60-day meteor.",
      },
      cycle: { ...SAMPLE_STATUS.cycle, daysUntilMeteor: null },
      achievements: [
        { name: "Saved the World", text: "Destroyed the meteor on Day 57, 23:13" },
      ],
    }
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(live), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    )

    renderHome()

    await waitFor(() => {
      expect(screen.getByTestId("home-meteor")).toHaveTextContent("Meteor destroyed on day 57 at 23:13")
    })
    const facts = screen.getByTestId("home-facts")
    expect(facts).toHaveTextContent("Cycle14")
    expect(facts).toHaveTextContent("World size100 × 100")
    expect(facts).toHaveTextContent("Meteor timer60 days")
    expect(facts).toHaveTextContent("CollaborationHigh")
  })
})
