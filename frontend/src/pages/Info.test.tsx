import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { MemoryRouter } from "react-router-dom"
import Info from "./Info"
import { SAMPLE_STATUS } from "../test/fixtures"

function renderServer(initialEntry = "/info") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Info />
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

describe("Info", () => {
  it("renders the live snapshot from /preview.json", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(SAMPLE_STATUS), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    )

    renderServer()

    expect(
      screen.getByRole("heading", { name: "Inspect another Eco server" }),
    ).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByTestId("meteor-count")).toHaveTextContent("3 days until the meteor")
    })
    expect(screen.getByTestId("live-pill")).toHaveTextContent("1 online now")
    expect(screen.getByTestId("online-player-list")).toHaveTextContent("coilysiren")
    expect(screen.getByText("Eco via Sirens | Cycle 13")).toBeInTheDocument()
    expect(screen.getByText("64,342")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Join the Discord" })).toHaveAttribute(
      "href",
      "https://discord.gg/example",
    )
  })

  it("says the meteor is destroyed instead of counting down to it", async () => {
    const destroyed = {
      ...SAMPLE_STATUS,
      cycle: { ...SAMPLE_STATUS.cycle, daysUntilMeteor: null },
      achievements: [
        { name: "Saved the World", text: "Destroyed the meteor on a server.\nDestroyed the meteor on Day 57, 23:13" },
      ],
    }
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(destroyed), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    )

    renderServer()

    await waitFor(() => {
      expect(screen.getByTestId("meteor-destroyed")).toHaveTextContent("Destroyed on day 57 at 23:13.")
    })
    expect(screen.queryByText(/a meteor is coming/i)).not.toBeInTheDocument()
  })

  it("inspects another public Eco server through the shared preview endpoint", async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify(SAMPLE_STATUS), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    )
    vi.stubGlobal("fetch", fetchMock)

    renderServer()

    fireEvent.change(screen.getByRole("textbox", { name: "Eco server address" }), {
      target: { value: "eco.example.test:3001" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Inspect server" }))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/preview.json?server=eco.example.test%3A3001",
        expect.objectContaining({ signal: expect.anything() }),
      )
    })
    expect(screen.getByTestId("server-target")).toHaveTextContent(
      "Inspecting eco.example.test:3001",
    )
    expect(screen.getByRole("button", { name: "Use Sirens server" })).toBeInTheDocument()
  })

  it("keeps the shell useful when the snapshot fetch fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("boom")))

    renderServer()

    await waitFor(() => {
      expect(screen.getByTestId("live-pill")).toHaveTextContent("live snapshot unavailable")
    })
    expect(screen.getByRole("link", { name: "Eco on Steam" })).toBeInTheDocument()
    expect(screen.queryByTestId("meteor-count")).not.toBeInTheDocument()
  })
})
