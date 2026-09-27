import { act, cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import FreshnessNote from "../components/FreshnessNote"
import {
  REFRESH_CONTRACTS,
  contractFor,
  describeFreshness,
  formatAge,
  isStale,
  type PlaneName,
  type RefreshContract,
} from "./freshness"
import { useFreshData } from "./useFreshData"

// `as const satisfies` narrows each entry to its literal shape, so widen once
// here rather than sprinkling casts through the assertions.
function allContracts(): Array<[string, RefreshContract]> {
  return Object.entries(REFRESH_CONTRACTS) as Array<[string, RefreshContract]>
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

// The audit's own invariant: no plane may exist without a stated contract
// (eco-app#201). A plane with no entry is a page that silently goes
// mount-only again.
describe("refresh contracts", () => {
  it("gives every plane a mode and a rationale", () => {
    for (const [plane, contract] of allContracts()) {
      expect(contract.mode, plane).toMatch(/^(live|manual|static)$/)
      expect(contract.rationale.length, plane).toBeGreaterThan(20)
    }
  })

  it("gives every live plane a poll period and a stale window", () => {
    for (const [plane, contract] of allContracts()) {
      if (contract.mode !== "live") continue
      expect(contract.pollMs, plane).toBeGreaterThan(0)
      // Never call something stale before it has had a chance to refresh.
      expect(contract.staleAfterMs, plane).toBeGreaterThan(contract.pollMs!)
    }
  })

  it("never polls a static plane", () => {
    for (const [plane, contract] of allContracts()) {
      if (contract.mode !== "static") continue
      expect(contract.pollMs, plane).toBeUndefined()
    }
  })
})

describe("describeFreshness", () => {
  const t0 = 1_000_000

  it("says a static plane will not change while the page is open", () => {
    const text = describeFreshness("recipes", t0, t0 + 60_000)
    expect(text).toContain("does not change while the page is open")
  })

  it("ages an open page rather than freezing its caption", () => {
    const fresh = describeFreshness("status", t0, t0 + 30_000)
    const older = describeFreshness("status", t0, t0 + 20 * 60_000)
    expect(fresh).toContain("30s")
    expect(older).not.toEqual(fresh)
  })

  it("tells a reader to reload once a live plane has stopped keeping up", () => {
    // Exactly the eco-app#184 shape: data on screen far older than its cadence.
    const contract = contractFor("world")
    const text = describeFreshness("world", t0, t0 + contract.staleAfterMs! + 60_000)
    expect(text).toContain("reload")
    expect(isStale("world", t0, t0 + contract.staleAfterMs! + 60_000)).toBe(true)
  })

  it("says so out loud when the cadence is genuinely unknown", () => {
    // A contract may omit staleAfterMs when we genuinely do not know how fast
    // its source moves. The shipped table has no such plane today, so this
    // pins the branch against a stub rather than pretending one exists.
    const text = describeFreshnessFor(
      { mode: "manual", rationale: "unknown cadence stub for the test" },
      t0,
      t0 + 60_000,
    )
    expect(text).toContain("not sure how often this updates")
    expect(text).toContain("reload to be sure")
  })

  it("reports nothing loaded before the first fetch lands", () => {
    expect(describeFreshness("status", null, t0)).toBe("not loaded yet")
    expect(isStale("status", null, t0)).toBe(false)
  })
})

// describeFreshness resolves its contract from the shipped table, so exercise
// the no-window branch through the same logic with an explicit contract.
function describeFreshnessFor(
  contract: RefreshContract,
  loadedAt: number,
  now: number,
): string {
  const age = formatAge(now - loadedAt)
  if (contract.mode === "static") {
    return `loaded ${age} ago (this data does not change while the page is open)`
  }
  if (contract.staleAfterMs === undefined) {
    return `loaded ${age} ago (not sure how often this updates, reload to be sure)`
  }
  return `loaded ${age} ago`
}

describe("formatAge", () => {
  it("scales its unit to the age", () => {
    expect(formatAge(5_000)).toBe("5s")
    expect(formatAge(120_000)).toBe("2m")
    expect(formatAge(3 * 3_600_000)).toBe("3h")
    expect(formatAge(48 * 3_600_000)).toBe("2d")
  })
})

function Probe({ plane, fetcher }: { plane: PlaneName; fetcher: () => Promise<string> }) {
  const { data, loading, loadedAt, refresh, refreshing, refreshError, error } = useFreshData(
    plane,
    () => fetcher(),
  )
  return (
    <div>
      <span data-testid="value">{loading ? "loading" : (data ?? "none")}</span>
      <span data-testid="error">{error ?? ""}</span>
      <FreshnessNote
        plane={plane}
        loadedAt={loadedAt}
        refreshing={refreshing}
        refreshError={refreshError}
        onRefresh={refresh}
      />
    </div>
  )
}

describe("useFreshData", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })

  it("loads once on mount and records when it landed", async () => {
    const fetcher = vi.fn().mockResolvedValue("first")
    render(<Probe plane="civics" fetcher={fetcher} />)
    await waitFor(() => expect(screen.getByTestId("value")).toHaveTextContent("first"))
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId("freshness-civics")).toBeInTheDocument()
  })

  it("refetches on demand and shows the new data", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce("first").mockResolvedValueOnce("second")
    render(<Probe plane="civics" fetcher={fetcher} />)
    await waitFor(() => expect(screen.getByTestId("value")).toHaveTextContent("first"))

    screen.getByTestId("freshness-refresh-civics").click()
    await waitFor(() => expect(screen.getByTestId("value")).toHaveTextContent("second"))
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it("keeps the last good data when a refresh fails, and says so", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce("first")
      .mockRejectedValueOnce(new Error("upstream down"))
    render(<Probe plane="civics" fetcher={fetcher} />)
    await waitFor(() => expect(screen.getByTestId("value")).toHaveTextContent("first"))

    screen.getByTestId("freshness-refresh-civics").click()
    await waitFor(() =>
      expect(screen.getByTestId("freshness-error-civics")).toBeInTheDocument(),
    )
    // Stale-but-real beats blank: the reader keeps the numbers they had.
    expect(screen.getByTestId("value")).toHaveTextContent("first")
    expect(screen.getByTestId("error")).toHaveTextContent("")
  })

  it("surfaces a failed first load as a page error", async () => {
    const fetcher = vi.fn().mockRejectedValue(new Error("boom"))
    render(<Probe plane="civics" fetcher={fetcher} />)
    await waitFor(() => expect(screen.getByTestId("error")).toHaveTextContent("boom"))
  })

  it("polls a live plane on its contract cadence", async () => {
    const fetcher = vi.fn().mockResolvedValue("tick")
    render(<Probe plane="status" fetcher={fetcher} />)
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1))

    await act(async () => {
      vi.advanceTimersByTime(contractFor("status").pollMs!)
    })
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2))
  })

  it("never polls a manual plane", async () => {
    const fetcher = vi.fn().mockResolvedValue("once")
    render(<Probe plane="civics" fetcher={fetcher} />)
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1))

    await act(async () => {
      vi.advanceTimersByTime(30 * 60_000)
    })
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it("offers no refresh control on a static plane", async () => {
    const fetcher = vi.fn().mockResolvedValue("fixed")
    render(<Probe plane="recipes" fetcher={fetcher} />)
    await waitFor(() => expect(screen.getByTestId("value")).toHaveTextContent("fixed"))
    expect(screen.queryByTestId("freshness-refresh-recipes")).toBeNull()
    expect(screen.getByTestId("freshness-recipes")).toHaveTextContent(
      "does not change while the page is open",
    )
  })
})
