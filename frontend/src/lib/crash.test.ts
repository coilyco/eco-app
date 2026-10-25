import * as Sentry from "@sentry/react"
import { afterEach, describe, expect, it } from "vitest"
import { beforeSend, initCrashReporting, sentryOptions } from "./crash"

type Sent = Record<string, unknown>

const envelopes: Record<string, Sent[]> = {}

function startCapturing(): Sent[] {
  for (const type of ["event", "transaction", "log"]) envelopes[type] = []
  const events = envelopes.event
  Sentry.init({
    ...sentryOptions("https://public@example.invalid/1"),
    transport: () => ({
      send: async (envelope) => {
        for (const [header, item] of envelope[1]) {
          const type = (header as { type: string }).type
          if (type === "log") envelopes.log.push(...(item as { items: Sent[] }).items)
          else envelopes[type]?.push(item as Sent)
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

  it("sends every crash with no per-minute cap", async () => {
    const events = startCapturing()
    for (let i = 0; i < 60; i++) {
      window.onerror?.(`crash ${i}`, "app.js", 1, 1, new Error(`crash ${i}`))
    }
    await Sentry.flush(1000)
    expect(events).toHaveLength(60)
  })

  it("sends a page-load trace with the query string and typed text removed", async () => {
    startCapturing()
    const secret = ["MEMBER", "SECRET"].join("-")
    Sentry.startSpan({ name: "/item", op: "pageload", forceTransaction: true }, () => {
      Sentry.startSpan(
        { name: `GET /info?query=${secret}`, op: "http.client", attributes: { value: secret } },
        () => {},
      )
    })
    await Sentry.flush(1000)
    expect(envelopes.transaction.length).toBeGreaterThan(0)
    const serialized = JSON.stringify(envelopes.transaction)
    expect(serialized).toContain("GET /info")
    expect(serialized).not.toContain(secret)
  })

  it("sends console.error as a log and not as an error event", async () => {
    const events = startCapturing()
    console.error("chart failed to draw")
    console.warn("slow chart")
    await Sentry.flush(1000)
    expect(envelopes.log.map((log) => log.body)).toEqual(["chart failed to draw"])
    expect(events).toHaveLength(0)
  })

  it("filters a log attribute under a user-data key", async () => {
    startCapturing()
    const secret = ["MEMBER", "SECRET"].join("-")
    Sentry.logger.error("reply failed", { text: secret })
    await Sentry.flush(1000)
    expect(JSON.stringify(envelopes.log)).not.toContain(secret)
  })
})
