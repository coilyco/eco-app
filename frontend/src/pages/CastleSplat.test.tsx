import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import CastleSplat, { SPLAT_URL } from "./CastleSplat"

const startViewer = vi.fn()
vi.mock("../lib/splatViewer", () => ({ startViewer: (...args: unknown[]) => startViewer(...args) }))

function viewerStub() {
  return { destroy: vi.fn(), setPlaying: vi.fn(), reset: vi.fn(), turn: vi.fn(), zoom: vi.fn() }
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/cycle-14/castle"]}>
      <CastleSplat />
    </MemoryRouter>,
  )
}

// jsdom has no WebGL2, so the page's own check needs a stand-in to get past.
function withWebGL2() {
  vi.stubGlobal("WebGL2RenderingContext", class {})
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({} as unknown as RenderingContext)
}

function reducedMotion(on: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: on && query.includes("reduce"), media: query }))
}

beforeEach(() => {
  startViewer.mockReset()
  reducedMotion(false)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe("Castle splat page", () => {
  it("says so, and loads nothing, when the browser cannot draw 3D", async () => {
    renderPage()
    expect(await screen.findByTestId("castle-unsupported")).toHaveTextContent(/cannot draw 3D/i)
    expect(startViewer).not.toHaveBeenCalled()
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("The castle of La Croisée des Bois")
    expect(screen.queryByRole("group", { name: /view controls/i })).not.toBeInTheDocument()
  })

  it("keeps the facts and the instructions on the page in every state", async () => {
    renderPage()
    await screen.findByTestId("castle-unsupported")
    expect(screen.getByText("386,757, after trimming")).toBeInTheDocument()
    expect(screen.getByText(/drag to orbit/i)).toBeInTheDocument()
  })

  it("keeps the still on the stage in every state, so the stage is never empty", async () => {
    renderPage()
    await screen.findByTestId("castle-unsupported")
    expect(screen.getByRole("img", { name: /a still from the 3D capture/i })).toBeInTheDocument()
  })

  it("shows a failure with a way to retry, and retries the same file", async () => {
    withWebGL2()
    startViewer.mockRejectedValueOnce(new Error("blocked by CORS")).mockResolvedValueOnce(viewerStub())
    renderPage()

    const alert = await screen.findByTestId("castle-failed")
    expect(alert).toHaveAttribute("role", "alert")
    expect(startViewer).toHaveBeenCalledTimes(1)
    expect(startViewer.mock.calls[0][1]).toBe(SPLAT_URL)

    fireEvent.click(screen.getByRole("button", { name: /try again/i }))
    await waitFor(() => expect(startViewer).toHaveBeenCalledTimes(2))
    expect(await screen.findByRole("group", { name: /view controls/i })).toBeInTheDocument()
    expect(screen.queryByTestId("castle-failed")).not.toBeInTheDocument()
  })

  it("offers the controls once the splat is up, and wires them to the viewer", async () => {
    withWebGL2()
    const viewer = viewerStub()
    startViewer.mockResolvedValue(viewer)
    renderPage()

    const pause = await screen.findByRole("button", { name: /pause orbit/i })
    fireEvent.click(pause)
    expect(viewer.setPlaying).toHaveBeenLastCalledWith(false)
    expect(screen.getByRole("button", { name: /play orbit/i })).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: /reset view/i }))
    expect(viewer.reset).toHaveBeenCalledTimes(1)
    expect(screen.getByRole("img", { name: /3D view of the castle/i })).toHaveAttribute("tabindex", "0")
  })

  it("starts paused for someone who asked for reduced motion", async () => {
    withWebGL2()
    reducedMotion(true)
    startViewer.mockResolvedValue(viewerStub())
    renderPage()

    expect(await screen.findByRole("button", { name: /play orbit/i })).toBeInTheDocument()
    expect(startViewer.mock.calls[0][2]).toEqual({ playing: false })
  })

  it("tears the viewer down on leaving, even when it finished loading late", async () => {
    withWebGL2()
    const viewer = viewerStub()
    let finish: (v: ReturnType<typeof viewerStub>) => void = () => {}
    startViewer.mockReturnValue(new Promise((resolve) => (finish = resolve)))
    const { unmount } = renderPage()
    await waitFor(() => expect(startViewer).toHaveBeenCalled())

    unmount()
    await act(async () => finish(viewer))
    expect(viewer.destroy).toHaveBeenCalledTimes(1)
  })

  it("offers both flythroughs as play buttons, with no video in the page until one is pressed", async () => {
    renderPage()
    await screen.findByTestId("castle-unsupported")
    expect(document.querySelectorAll("video")).toHaveLength(0)
    const play = screen.getByRole("button", { name: /play flythrough 1, 55 seconds, 27 MB/i })
    expect(play.querySelector("img")?.getAttribute("src")).toMatch(/flythrough-1-poster\.jpg$/)
    expect(screen.getByRole("button", { name: /play flythrough 2, 30 seconds, 25 MB/i })).toBeInTheDocument()
    expect(screen.getByText("Flythrough 2, 30 seconds, 25 MB")).toBeInTheDocument()
  })

  it("drops a poster that will not load, and keeps the labelled button", async () => {
    renderPage()
    await screen.findByTestId("castle-unsupported")
    const play = screen.getByRole("button", { name: /play flythrough 1/i })
    fireEvent.error(play.querySelector("img") as HTMLImageElement)
    expect(play.querySelector("img")).toBeNull()
    expect(screen.getByRole("button", { name: /play flythrough 1/i })).toBeInTheDocument()
  })

  it("puts the video in on press, and only the one that was pressed", async () => {
    renderPage()
    await screen.findByTestId("castle-unsupported")
    fireEvent.click(screen.getByRole("button", { name: /play flythrough 2/i }))

    const videos = document.querySelectorAll("video")
    expect(videos).toHaveLength(1)
    expect(videos[0].getAttribute("src")).toMatch(/flythrough-2\.mp4$/)
    expect(videos[0]).toHaveAttribute("controls")
    expect(screen.getByRole("button", { name: /play flythrough 1/i })).toBeInTheDocument()
  })

  it("says so when a flythrough will not load, and tries again on request", async () => {
    renderPage()
    await screen.findByTestId("castle-unsupported")
    fireEvent.click(screen.getByRole("button", { name: /play flythrough 1/i }))
    fireEvent.error(screen.getByLabelText("Flythrough 1, 55 seconds"))

    const alert = await screen.findByTestId("castle-video-failed-1")
    expect(alert).toHaveAttribute("role", "alert")
    expect(screen.getByRole("button", { name: /play flythrough 2/i })).toBeInTheDocument()

    fireEvent.click(within(alert).getByRole("button", { name: /try again/i }))
    expect(await screen.findByLabelText("Flythrough 1, 55 seconds")).toBeInTheDocument()
    expect(screen.queryByTestId("castle-video-failed-1")).not.toBeInTheDocument()
  })
})
