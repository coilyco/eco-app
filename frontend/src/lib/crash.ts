// Sentry for uncaught errors and render crashes (fully annotated), page-load and
// navigation traces, and console.error as Sentry Logs, with user-data keys
// scrubbed from all of them (COI-2905).
import * as Sentry from "@sentry/react"
import type { Breadcrumb, BrowserOptions, ErrorEvent, Log } from "@sentry/react"

type TransactionEvent = Parameters<NonNullable<BrowserOptions["beforeSendTransaction"]>>[0]

const SCRUBBED = "[Filtered]"

// Names that hold what a visitor typed, or a credential for the admin pages.
export const USER_DATA_KEYS = new Set([
  "value",
  "input",
  "text",
  "query",
  "search",
  "email",
  "username",
  "password",
  "token",
  "auth",
  "authorization",
  "cookie",
  "session",
])

// Team plan: 5M spans a month. The pod's access log on 2026-10-25 showed under one
// SPA page load an hour, so sampling every load is a few thousand spans a month.
// Re-size from Sentry's span stats if traffic grows.
export const TRACES_SAMPLE_RATE = 1

export function scrub(value: unknown, depth = 0): unknown {
  if (depth > 8 || value === null || typeof value !== "object") return value
  if (Array.isArray(value)) return value.map((item) => scrub(item, depth + 1))
  const out: Record<string, unknown> = {}
  for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
    out[key] = USER_DATA_KEYS.has(key.toLowerCase()) ? SCRUBBED : scrub(item, depth + 1)
  }
  return out
}

export function beforeBreadcrumb(crumb: Breadcrumb): Breadcrumb {
  return crumb.data ? { ...crumb, data: scrub(crumb.data) as Breadcrumb["data"] } : crumb
}

// Only the parts that can carry user data are scrubbed, so the exception stays readable.
function scrubEvent<T extends ErrorEvent | TransactionEvent>(event: T): T {
  if (event.request) event.request = scrub(event.request) as T["request"]
  if (event.extra) event.extra = scrub(event.extra) as T["extra"]
  if (event.contexts) event.contexts = scrub(event.contexts) as T["contexts"]
  if (event.breadcrumbs) event.breadcrumbs = event.breadcrumbs.map(beforeBreadcrumb)
  return event
}

export function beforeSend(event: ErrorEvent): ErrorEvent | null {
  return scrubEvent(event)
}

// A fetch span is named for its URL, and a query string holds what the visitor searched.
function withoutQuery(value: unknown): unknown {
  return typeof value === "string" ? value.replace(/\?[^\s#]*/, "") : value
}

export function beforeSendTransaction(event: TransactionEvent): TransactionEvent | null {
  for (const span of event.spans ?? []) {
    span.description = withoutQuery(span.description) as string | undefined
    span.data = scrub(span.data) as typeof span.data
    for (const key of ["url", "url.full", "http.url", "http.query", "http.fragment"]) {
      if (key in span.data) span.data[key] = withoutQuery(span.data[key]) as string
    }
  }
  event.transaction = withoutQuery(event.transaction) as string
  return scrubEvent(event)
}

export function beforeSendLog(log: Log): Log {
  return log.attributes ? { ...log, attributes: scrub(log.attributes) as Log["attributes"] } : log
}

// Default integrations stay on for annotation. None of them turns a handled
// error or a console line into an event: console.error becomes a log, not an error.
export function sentryOptions(dsn: string): BrowserOptions {
  return {
    dsn,
    sendDefaultPii: false,
    tracesSampleRate: TRACES_SAMPLE_RATE,
    enableLogs: true,
    integrations: [
      Sentry.browserTracingIntegration(),
      Sentry.consoleLoggingIntegration({ levels: ["error"] }),
    ],
    beforeSend,
    beforeSendTransaction,
    beforeSendLog,
    beforeBreadcrumb,
  }
}

export function initCrashReporting(dsn: string | undefined): boolean {
  if (!dsn) return false
  try {
    Sentry.init(sentryOptions(dsn))
    return true
  } catch (error) {
    // The name only: a DSN parse error can carry the DSN.
    console.warn(`Sentry initialization failed (${(error as Error).name}); continuing`)
    return false
  }
}

// React 19 root hook for render crashes. Errors a boundary catches are handled.
export const onUncaughtError = Sentry.reactErrorHandler()
