import { useEffect, useState, type FormEvent, type ReactNode } from "react"
import Layout from "./Layout"
import Loading from "./Loading"

// Soft password gate for the URL-only surfaces (/replay, /social). Asks the
// service whether a password is configured (`GET /page-auth`); if so, and the
// browser hasn't unlocked before, it shows a prompt and verifies the answer
// server-side (`POST /page-auth`) so the password never ships to the client.
// This is a speed bump, not a security boundary — the JSON APIs behind these
// pages stay public (eco-app#73).

const STORAGE_KEY = "eco-app:page-unlocked"

type GateState = "checking" | "locked" | "unlocked"

function alreadyUnlocked(): boolean {
  return typeof localStorage !== "undefined" && localStorage.getItem(STORAGE_KEY) === "1"
}

export default function PagePassword({ children }: { children: ReactNode }) {
  // A prior unlock in this browser skips the round-trip and the prompt — seed
  // it in the initializer so the effect never has to setState synchronously.
  const [state, setState] = useState<GateState>(() => (alreadyUnlocked() ? "unlocked" : "checking"))
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (alreadyUnlocked()) return
    const controller = new AbortController()
    fetch("/page-auth", { signal: controller.signal })
      .then((r) => r.json())
      .then((data: { required?: boolean }) => {
        setState(data.required ? "locked" : "unlocked")
      })
      .catch((err) => {
        // Can't reach the gate check — fail closed so the surface stays
        // behind the prompt, but a correct password will still let you in.
        if (err instanceof DOMException && err.name === "AbortError") return
        setState("locked")
      })
    return () => controller.abort()
  }, [])

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const r = await fetch("/page-auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      })
      const data = (await r.json()) as { ok?: boolean }
      if (r.ok && data.ok) {
        try {
          localStorage.setItem(STORAGE_KEY, "1")
        } catch {
          // Private-mode / disabled storage: unlock for this view anyway.
        }
        setState("unlocked")
      } else {
        setError("That password didn't match. Try again.")
      }
    } catch {
      setError("Couldn't verify right now. Check your connection and retry.")
    } finally {
      setSubmitting(false)
    }
  }

  if (state === "checking") {
    return (
      <Layout>
        <Loading label="Checking access…" testid="gate-checking" />
      </Layout>
    )
  }

  if (state === "unlocked") {
    return <>{children}</>
  }

  return (
    <Layout>
      <section className="gate" data-testid="page-gate">
        <h1 className="gate-title">Password required</h1>
        <p className="gate-note">
          This is a private, URL-only surface. Enter the page password to continue.
        </p>
        <form className="gate-form" onSubmit={onSubmit}>
          <input
            className="filter-input gate-input"
            type="password"
            autoFocus
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Page password"
            aria-label="Page password"
            data-testid="gate-input"
          />
          <button
            className="button button-primary"
            type="submit"
            disabled={submitting || password.length === 0}
            data-testid="gate-submit"
          >
            {submitting ? "Checking…" : "Unlock"}
          </button>
        </form>
        {error && (
          <p className="gate-error" role="alert" data-testid="gate-error">
            {error}
          </p>
        )}
      </section>
    </Layout>
  )
}
