import { useEffect, useRef } from "react"

// What a player sees when a page throws while rendering. Without it the root
// unmounts and the screen goes blank, with nothing to read or press. It sits
// outside Layout on purpose, since the crash may be in Layout itself.
export default function PageCrash() {
  const heading = useRef<HTMLHeadingElement>(null)

  // Move focus to the message so a keyboard or screen reader user lands on it.
  useEffect(() => heading.current?.focus(), [])

  return (
    <div className="page k-page">
      <main className="content">
        <section className="hero hero-compact" role="alert" data-testid="page-crash">
          <p className="hero-kicker">Eco via Sirens</p>
          <h1 className="hero-title" tabIndex={-1} ref={heading}>
            This page <span className="accent">broke</span>
          </h1>
          <p className="hero-tagline">
            Something went wrong while drawing it, and nothing you did caused it. Reload to try again, or
            go back to the home page.
          </p>
          <p>
            <button className="button button-primary" type="button" onClick={() => window.location.reload()}>
              Reload this page
            </button>{" "}
            <a className="button" href="/">
              Home
            </a>
          </p>
        </section>
      </main>
    </div>
  )
}
