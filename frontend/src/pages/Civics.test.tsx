import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { MemoryRouter } from "react-router-dom"
import Civics from "./Civics"

const REPORT = {
  fetchedAtISO: "2026-06-12T13:00:00+00:00",
  sourceBaseUrl: "http://x:3001",
  adminAvailable: true,
  unavailableActions: [],
  measurementNote: "",
  totalEvents: 12,
  perActionCounts: { Vote: 3, DidntVote: 1 },
  electionsStarted: 1,
  electionsWon: 1,
  electionsLost: 0,
  votesCast: 3,
  abstentions: 1,
  turnoutRate: 0.75,
  recentElections: [
    { subject: "MayorRace", subjectId: null, proposer: "alice", proposerId: null, day: 3 },
  ],
  recentOutcomes: [
    { subject: "MayorRace", subjectId: null, winner: "alice", winnerId: null, day: 3 },
  ],
  topVoters: [
    ["alice", 2],
    ["bob", 1],
  ],
  citizensGained: 2,
  citizensLost: 1,
  netCitizens: 1,
  distinctCitizensGained: 2,
  distinctCitizensLost: 1,
  netDistinctCitizens: 1,
  duplicateDemographicEvents: 0,
  demographicsNote: "citizensGained / citizensLost count exporter events.",
  residencyMoves: 1,
  demographicChanges: 0,
  recentDemographics: [
    { name: "bob", nameId: null, day: 2, kind: "joined", settlement: "Rivertown", settlementId: null },
    // An id the citizens join missed: null name, raw id alongside (eco-app#223).
    { name: null, nameId: "104", day: 2, kind: "left", settlement: "Rivertown", settlementId: null },
  ],
  settlementsFounded: 1,
  settlementFoundationsPlaced: 3,
  homesteadsStarted: 1,
  recentSettlements: [
    { subject: "Rivertown", subjectId: null, founder: "alice", founderId: null, day: 2, kind: "settlement" },
    { subject: "BobsFarm", subjectId: null, founder: "bob", founderId: null, day: 2, kind: "homestead" },
  ],
  trend: {
    Vote: [
      [0, 0],
      [1, 1],
      [2, 3],
    ],
    DidntVote: [
      [1, 0],
      [2, 1],
    ],
  },
  warnings: [],
}

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })
}

function stubFetch(payload: unknown = REPORT) {
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(payload))))
}

function renderCivics(entry = "/civics") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Civics />
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe("Civics", () => {
  it("renders the civic snapshot and turnout", async () => {
    stubFetch()
    renderCivics()

    await waitFor(() => {
      expect(screen.getByTestId("civics-pill")).toHaveTextContent("12 civic events")
    })
    expect(screen.getByTestId("civics-pill")).toHaveTextContent("75% voter turnout")
    expect(screen.getByTestId("civics-stats")).toHaveTextContent("Voter turnout")
  })

  it("lists recent elections with proposer names", async () => {
    stubFetch()
    renderCivics()

    await waitFor(() => {
      expect(screen.getByTestId("elections-table")).toBeInTheDocument()
    })
    expect(screen.getByTestId("election-row")).toHaveTextContent("MayorRace")
    expect(screen.getByTestId("election-row")).toHaveTextContent("alice")
  })

  it("shows an unresolved proposer as its raw id, never as a named citizen", async () => {
    stubFetch({
      ...REPORT,
      recentElections: [
        // An id the citizens join missed: null name, raw id alongside (eco-app#223).
        { subject: "MayorRace", subjectId: null, proposer: null, proposerId: "104", day: 3 },
      ],
    })
    renderCivics()

    await waitFor(() => {
      expect(screen.getByTestId("elections-table")).toBeInTheDocument()
    })
    // Some of those ids are election titles, not people (eco-app#223).
    expect(screen.getByText("#104")).toBeInTheDocument()
    expect(screen.queryByText("Citizen #104")).toBeNull()
  })

  it("shows an empty state when no civic events are recorded", async () => {
    stubFetch({
      ...REPORT,
      totalEvents: 0,
      recentElections: [],
      recentSettlements: [],
      recentDemographics: [],
      topVoters: [],
      trend: {},
    })
    renderCivics()

    await waitFor(() => {
      expect(screen.getByTestId("civics-empty")).toBeInTheDocument()
    })
  })

  it("says nothing was measured when no exporter could be read", async () => {
    // Every scalar null with adminAvailable false is an auth failure, not a
    // quiet server — the two must not render the same (eco-app#259).
    stubFetch({
      ...REPORT,
      adminAvailable: false,
      unavailableActions: ["Vote", "BecomeCitizen"],
      measurementNote: "A null scalar means its exporter could not be read.",
      totalEvents: null,
      votesCast: null,
      abstentions: null,
      turnoutRate: null,
      recentElections: [],
      recentSettlements: [],
      recentDemographics: [],
      topVoters: [],
      trend: {},
    })
    renderCivics()

    await waitFor(() => {
      expect(screen.getByTestId("civics-unmeasured")).toBeInTheDocument()
    })
    expect(screen.queryByTestId("civics-empty")).toBeNull()
  })

  it("degrades when the report fetch fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("boom")))
    renderCivics()

    await waitFor(() => {
      expect(screen.getByTestId("civics-error")).toBeInTheDocument()
    })
  })
})
