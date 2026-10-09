import { useEffect, useRef, useState } from "react"
import { Link } from "react-router-dom"
import Layout from "../components/Layout"
import Loading from "../components/Loading"
import type { SplatViewer, ViewSpec } from "../lib/splatViewer"

// One Gaussian splat of a cycle 14 build: a still on the stage, a live view over it,
// and the facts of the capture. The splat is too big to commit (the repo caps files at
// 2 MB), so it loads from the files host. A new build is a config and a one-line page,
// like BioDriveSplat. docs/frontend/biodrive-splat.md. The markup and styles are the
// castle page's, docs/frontend/castle-splat.md.
export interface SplatConfig {
  /** Names the test ids and the viewer's asset, like `biodrive`. */
  slug: string
  /** What the page and the loading line call it, like `the BioDrive station`. */
  subject: string
  title: string
  caption: string
  /** The still on the stage, 1920x1080, and what it shows. */
  hero: string
  heroAlt: string
  splatUrl: string
  /** The splat's size as the loading line says it, like `14.8 MB`. */
  size: string
  facts: Array<[string, string]>
  view: ViewSpec
}

type Phase = "unsupported" | "loading" | "ready" | "failed"

// WebGL2 is the floor the engine needs. Checking the global first keeps jsdom
// (which has no canvas) from logging a not-implemented error.
function canDrawIn3d(): boolean {
  if (typeof WebGL2RenderingContext === "undefined") return false
  try {
    return Boolean(document.createElement("canvas").getContext("webgl2"))
  } catch {
    return false
  }
}

const prefersReducedMotion = () =>
  typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches

export default function SplatPage({ config }: { config: SplatConfig }) {
  const { slug, subject, title, caption, hero, heroAlt, splatUrl, size, facts, view } = config
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const viewerRef = useRef<SplatViewer | null>(null)
  const [phase, setPhase] = useState<Phase>(() => (canDrawIn3d() ? "loading" : "unsupported"))
  const [attempt, setAttempt] = useState(0)
  const [playing, setPlaying] = useState(() => !prefersReducedMotion())
  const playingAtStart = useRef(playing)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !canDrawIn3d()) return
    let cancelled = false
    // Deferred a tick so React's dev double-mount cancels the first start before
    // it boots a second engine on the same canvas.
    const timer = window.setTimeout(() => {
      import("../lib/splatViewer")
        .then(({ startViewer }) => startViewer(canvas, splatUrl, { playing: playingAtStart.current, view }))
        .then((viewer) => {
          if (cancelled) {
            viewer.destroy()
            return
          }
          viewerRef.current = viewer
          setPhase("ready")
        })
        .catch(() => {
          if (!cancelled) setPhase("failed")
        })
    }, 0)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
      viewerRef.current?.destroy()
      viewerRef.current = null
    }
  }, [attempt, splatUrl, view])

  useEffect(() => {
    viewerRef.current?.setPlaying(playing)
  }, [playing, phase])

  const live = phase === "ready"
  const plain = subject.replace(/^the /, "")

  return (
    <Layout>
      <section className="k-stack k-stack--4">
        <h1 className="k-display">{title}</h1>
        <p className="castle-sub">{caption}</p>
      </section>

      <section className="k-stack k-stack--4 castle" aria-labelledby={`${slug}-view`}>
        <h2 className="k-h2" id={`${slug}-view`}>
          Look around
        </h2>
        <div className="castle-stage" data-phase={phase}>
          {/* The still stands in while the splat loads and when it cannot, so the stage is never empty. */}
          <img className="castle-still" src={hero} width={1920} height={1080} alt={heroAlt} />
          <canvas
            ref={canvasRef}
            className={phase === "failed" || phase === "unsupported" ? "castle-canvas castle-canvas--off" : "castle-canvas"}
            // The canvas draws pixels no assistive tech can read, so it is one
            // labelled image with its controls described beside it.
            role="img"
            aria-label={`3D view of ${subject}, a Gaussian splat. The facts below describe the capture.`}
            aria-describedby={`${slug}-help`}
            tabIndex={live ? 0 : -1}
            aria-hidden={!live}
          />
          {phase === "loading" ? <Loading label={`Loading ${subject}, ${size}…`} testid={`${slug}-loading`} /> : null}
          {phase === "unsupported" ? (
            <p className="castle-note" role="status" data-testid={`${slug}-unsupported`}>
              This browser cannot draw 3D with WebGL 2, so the 3D {plain} cannot show here. A recent desktop browser can.
            </p>
          ) : null}
          {phase === "failed" ? (
            <div className="castle-note k-stack k-stack--3" role="alert" data-testid={`${slug}-failed`}>
              <p>The {plain} did not load. The file may be unavailable right now.</p>
              <div className="k-btn-row">
                <button
                  type="button"
                  className="k-btn k-btn--quiet"
                  onClick={() => {
                    setPhase("loading")
                    setAttempt((n) => n + 1)
                  }}
                >
                  Try again
                </button>
              </div>
            </div>
          ) : null}
        </div>
        {live ? (
          <div className="k-btn-row" role="group" aria-label="View controls">
            <button type="button" className="k-btn k-btn--quiet" onClick={() => setPlaying((p) => !p)}>
              {playing ? "Pause orbit" : "Play orbit"}
            </button>
            <button type="button" className="k-btn k-btn--quiet" onClick={() => viewerRef.current?.reset()}>
              Reset view
            </button>
          </div>
        ) : null}
        <p className="k-hint" id={`${slug}-help`}>
          Drag to orbit, scroll or pinch to zoom. With the view focused, the arrow keys turn it and plus and minus
          zoom.
        </p>
      </section>

      <section className="k-stack k-stack--3" aria-labelledby={`${slug}-facts`}>
        <h2 className="k-h2" id={`${slug}-facts`}>
          About the capture
        </h2>
        <dl className="k-deflist">
          {facts.map(([term, detail]) => (
            <div key={term}>
              <dt>{term}</dt>
              <dd>{detail}</dd>
            </div>
          ))}
        </dl>
      </section>
      <p className="k-hint">
        <Link to="/cycle-14">All cycle 14 scenes</Link>
      </p>
    </Layout>
  )
}
