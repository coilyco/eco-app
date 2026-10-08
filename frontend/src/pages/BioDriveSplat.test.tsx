import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import BioDriveSplat, { BIODRIVE, SPLAT_URL } from "./BioDriveSplat"

const startViewer = vi.fn()
vi.mock("../lib/splatViewer", () => ({ startViewer: (...args: unknown[]) => startViewer(...args) }))

function viewerStub() {
  return { destroy: vi.fn(), setPlaying: vi.fn(), reset: vi.fn(), turn: vi.fn(), zoom: vi.fn() }
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/cycle-14/biodrive"]}>
      <BioDriveSplat />
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

describe("BioDrive splat page", () => {
  it("says so, and loads nothing, when the browser cannot draw 3D", async () => {
    renderPage()
    expect(await screen.findByTestId("biodrive-unsupported")).toHaveTextContent(/cannot draw 3D/i)
    expect(startViewer).not.toHaveBeenCalled()
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("BioDrive station")
    expect(screen.getByText("Scuba Steve's BioDrive station in Phantom Springs, cycle 14.")).toBeInTheDocument()
    expect(screen.queryByRole("group", { name: /view controls/i })).not.toBeInTheDocument()
  })

  it("keeps the facts, the instructions and the still on the page in every state", async () => {
    renderPage()
    await screen.findByTestId("biodrive-unsupported")
    expect(screen.getByText("881,854, after trimming")).toBeInTheDocument()
    expect(screen.getByText(/drag to orbit/i)).toBeInTheDocument()
    expect(screen.getByRole("img", { name: /a still from the 3D capture/i })).toBeInTheDocument()
  })

  it("says what is loading, with the size, while the splat comes in", async () => {
    withWebGL2()
    startViewer.mockReturnValue(new Promise(() => {}))
    renderPage()
    expect(await screen.findByTestId("biodrive-loading")).toHaveTextContent("Loading the BioDrive station, 14.8 MB")
  })

  it("starts the viewer on the v2 splat with the station's own view", async () => {
    withWebGL2()
    startViewer.mockResolvedValue(viewerStub())
    renderPage()
    await screen.findByRole("group", { name: /view controls/i })
    expect(startViewer.mock.calls[0][1]).toBe(SPLAT_URL)
    expect(SPLAT_URL).toMatch(/\/biodrive-v2\.sog$/)
    expect(startViewer.mock.calls[0][2]).toEqual({ playing: true, view: BIODRIVE.view })
  })

  it("shows a failure with a way to retry, and retries the same file", async () => {
    withWebGL2()
    startViewer.mockRejectedValueOnce(new Error("blocked by CORS")).mockResolvedValueOnce(viewerStub())
    renderPage()

    const alert = await screen.findByTestId("biodrive-failed")
    expect(alert).toHaveAttribute("role", "alert")
    expect(alert).toHaveTextContent("The BioDrive station did not load.")
    expect(startViewer).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole("button", { name: /try again/i }))
    await waitFor(() => expect(startViewer).toHaveBeenCalledTimes(2))
    expect(startViewer.mock.calls[1][1]).toBe(SPLAT_URL)
    expect(await screen.findByRole("group", { name: /view controls/i })).toBeInTheDocument()
    expect(screen.queryByTestId("biodrive-failed")).not.toBeInTheDocument()
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
    expect(screen.getByRole("img", { name: /3D view of the BioDrive station/i })).toHaveAttribute("tabindex", "0")
  })

  it("starts paused for someone who asked for reduced motion", async () => {
    withWebGL2()
    reducedMotion(true)
    startViewer.mockResolvedValue(viewerStub())
    renderPage()

    expect(await screen.findByRole("button", { name: /play orbit/i })).toBeInTheDocument()
    expect(startViewer.mock.calls[0][2]).toEqual({ playing: false, view: BIODRIVE.view })
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
})
