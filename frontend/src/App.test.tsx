import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import App from "./App"

afterEach(cleanup)

describe("SPA routes", () => {
  it("serves the complete mod catalog on a direct /mods route", () => {
    window.history.pushState({}, "", "/mods")
    render(<App />)

    expect(screen.getByRole("heading", { name: "Every mod on the server" })).toBeInTheDocument()
  })

  it("serves the official Wiki snapshot on a direct /wiki route", () => {
    window.history.pushState({}, "", "/wiki")
    render(<App />)

    expect(screen.getByRole("heading", { name: "Official Eco Wiki snapshot" })).toBeInTheDocument()
  })
})
