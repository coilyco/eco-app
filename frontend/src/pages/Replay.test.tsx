import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { MemoryRouter } from "react-router-dom"
import Replay from "./Replay"

const EVENTS = [
  {
    id: 3,
    unixTime: 1779000300,
    gameTime: 100200,
    type: "PlaceBlock",
    citizen: "Kai",
    body: JSON.stringify({ position: "(120, 64, -85)", block: "Sandstone" }),
  },
  {
    id: 2,
    unixTime: 1779000200,
    gameTime: 100100,
    type: "ChatMessage",
    citizen: "Mira",
    body: JSON.stringify({ channel: "#general", message: "anyone selling iron ingots?" }),
  },
  {
    id: 1,
    unixTime: 1779000100,
    gameTime: 100000,
    type: "Login",
    citizen: "Kai",
    body: "{}",
  },
]

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })
}

// The page fires three fetches: meta, events, and stats. Route each by URL.
function stubReplayFetch(opts: { events?: unknown[]; total?: number; mockData?: boolean } = {}) {
  const events = opts.events ?? EVENTS
  const total = opts.total ?? events.length
  const mockData = opts.mockData ?? false
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      const u = String(url)
      if (u.includes("/replay/api/v1/meta")) return Promise.resolve(jsonResponse({ mockData }))
      if (u.includes("/replay/api/v1/events/stats"))
        return Promise.resolve(jsonResponse({ ready: true, total }))
      return Promise.resolve(jsonResponse({ events, count: events.length }))
    }),
  )
}

function renderReplay(entry = "/replay") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Replay />
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe("Replay", () => {
  it("renders the total pill and the timeline rows", async () => {
    stubReplayFetch()
    renderReplay()

    await waitFor(() => {
      expect(screen.getByTestId("replay-pill")).toHaveTextContent("3 events recorded")
    })
    expect(screen.getAllByTestId("replay-row")).toHaveLength(3)
    expect(screen.getByText("PlaceBlock")).toBeInTheDocument()
  })

  it("honors a ?q= deep link by filtering the timeline", async () => {
    stubReplayFetch()
    renderReplay("/replay?q=chatmessage")

    await waitFor(() => {
      expect(screen.getByTestId("replay-filter")).toHaveValue("chatmessage")
    })
    expect(screen.getAllByTestId("replay-row")).toHaveLength(1)
    expect(screen.getByText("ChatMessage")).toBeInTheDocument()
  })

  it("pushes an action-type cell click into the filter", async () => {
    stubReplayFetch()
    renderReplay()

    await waitFor(() => {
      expect(screen.getAllByTestId("replay-row")).toHaveLength(3)
    })
    fireEvent.click(screen.getByText("Login"))
    expect(screen.getByTestId("replay-filter")).toHaveValue("Login")
    expect(screen.getAllByTestId("replay-row")).toHaveLength(1)
  })

  it("shows the mock-data banner when the service has no real source", async () => {
    stubReplayFetch({ mockData: true })
    renderReplay()

    await waitFor(() => {
      expect(screen.getByTestId("mock-banner")).toBeInTheDocument()
    })
  })

  it("shows an empty state when no events are recorded", async () => {
    stubReplayFetch({ events: [], total: 0 })
    renderReplay()

    await waitFor(() => {
      expect(screen.getByTestId("replay-empty")).toBeInTheDocument()
    })
  })

  it("degrades when the chronicle fetch fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("boom")))
    renderReplay()

    await waitFor(() => {
      expect(screen.getByTestId("replay-error")).toBeInTheDocument()
    })
    expect(screen.queryByTestId("replay-pill")).not.toBeInTheDocument()
    expect(screen.queryByTestId("replay-table")).not.toBeInTheDocument()
    expect(screen.queryByTestId("mock-banner")).not.toBeInTheDocument()
  })
})
