import * as Sentry from "@sentry/react"
import { afterEach, describe, expect, it } from "vitest"
import { beforeSend, initCrashReporting, sentryOptions, withinBudget } from "./crash"

type Sent = Record<string, unknown>

function startCapturing(): Sent[] {
  const events: Sent[] = []
  Sentry.init({
    ...sentryOptions("https://public@example.invalid/1"),
    transport: () => ({
      send: async (envelope) => {
        for (const [header, item] of envelope[1]) {
          if ((header as { type: string }).type === "event") events.push(item as Sent)
        }
        return {}
      },
      flush: async () => true,
    }),
  })
  return events
}

describe("crash reporting", () => {
  afterEach(async () => {
    await Sentry.getClient()?.close()
  })

  it("stays off without a DSN", () => {
    expect(initCrashReporting("")).toBe(false)
    expect(initCrashReporting(undefined)).toBe(false)
  })

  it("sends an uncaught window error and not a handled one", async () => {
    const events = startCapturing()
    try {
      throw new Error("handled on purpose")
    } catch {
      // A handled failure is the app's business, not a crash.
    }
    // What the browser does with an error nothing caught.
    window.onerror?.("page crashed", "app.js", 1, 1, new Error("page crashed"))
    await Sentry.flush(1000)
    const values = events.map((event) => JSON.stringify(event))
    expect(values.some((value) => value.includes("page crashed"))).toBe(true)
    expect(values.some((value) => value.includes("handled on purpose"))).toBe(false)
  })

  it("scrubs typed and credential keys and keeps the exception readable", () => {
    const secret = ["ADMIN", "SECRET"].join("-")
    const event = beforeSend({
      type: undefined,
      exception: { values: [{ type: "TypeError", value: "chart crashed" }] },
      extra: { form: { password: secret, page: "/jobs" } },
      breadcrumbs: [{ category: "ui.input", data: { value: secret, target: "#search" } }],
    })
    expect(event?.exception?.values?.[0]?.value).toBe("chart crashed")
    expect(JSON.stringify(event)).not.toContain(secret)
    expect(JSON.stringify(event)).toContain("/jobs")
  })

  it("caps events per minute and recovers", () => {
    const later = Date.now() + 3_600_000
    const allowed = Array.from({ length: 21 }, () => withinBudget(later))
    expect(allowed.filter(Boolean)).toHaveLength(20)
    expect(withinBudget(later + 61_000)).toBe(true)
  })
})
