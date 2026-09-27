import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, describe, expect, it, vi } from "vitest"
import Species from "./Species"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe("Species page", () => {
  it("renders the linked species profile and current-cycle population curve", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            view: "eco_species",
            name: "Wolf",
            speciesId: "WolfSpecies",
            photoDataUri: null,
            photoUrl: null,
            photoAttribution: null,
            wikiExtract: "A social predator.",
            wikiUrl: "https://example.test/wolf",
            source: "wikipedia",
            taxonomy: [{ rank: "species", name: "Canis lupus" }],
            conservationStatus: null,
            population: [
              { day: 0, value: 100 },
              { day: 2, value: 40 },
            ],
            populationFirst: 100,
            populationLatest: 40,
            populationDelta: -60,
            error: null,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    )
    vi.stubGlobal("fetch", fetchMock)

    render(
      <MemoryRouter initialEntries={["/species?name=WolfSpecies"]}>
        <Species />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByTestId("species-population-curve")).toBeInTheDocument()
    })
    // No photo on the page, so no ~285 KB inlined image (eco-app#230, #8385).
    expect(fetchMock).toHaveBeenCalledWith(
      "/preview/get_species.json?name=WolfSpecies&limit=0",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
    expect(screen.getByRole("heading", { name: "Wolf" })).toBeInTheDocument()
    expect(screen.getByText(/40 current/)).toHaveTextContent("-60 this cycle")
  })
})
