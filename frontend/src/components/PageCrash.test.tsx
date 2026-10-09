import { cleanup, render, screen } from "@testing-library/react"
import * as Sentry from "@sentry/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import PageCrash from "./PageCrash"

afterEach(() => cleanup())

function Boom(): never {
  throw new Error("render crash")
}

describe("PageCrash", () => {
  it("replaces a crashed page with an announced message, a reload button and a way home", () => {
    // React logs the caught error, which is noise here.
    vi.spyOn(console, "error").mockImplementation(() => {})
    render(
      <Sentry.ErrorBoundary fallback={<PageCrash />}>
        <Boom />
      </Sentry.ErrorBoundary>,
    )
    const crash = screen.getByTestId("page-crash")
    expect(crash).toHaveAttribute("role", "alert")
    expect(crash).toHaveTextContent("This page broke")
    expect(screen.getByRole("button", { name: "Reload this page" })).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Home" })).toHaveAttribute("href", "/")
    expect(screen.getByRole("heading", { level: 1 })).toHaveFocus()
  })
})
