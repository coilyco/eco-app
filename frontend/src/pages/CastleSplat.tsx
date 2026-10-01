import { useEffect, useRef, useState } from "react"
import Layout from "../components/Layout"
import Loading from "../components/Loading"
import type { SplatViewer } from "../lib/splatViewer"

// A Gaussian splat of the cycle 14 castle draft. The file is too big to commit
// (the repo caps files at 2 MB), so it loads from the files host.
// docs/frontend/castle-splat.md.
export const SPLAT_URL = "https://files.coilysiren.me/eco/cycle-14/castle-draft.sog"

type Phase = "unsupported" | "loading" | "ready" | "failed"

const FACTS: Array<[string, string]> = [
  ["Frames", "72"],
  ["Reconstruction", "COLMAP 4.2.1"],
  ["Training", "Brush v0.3.0, 15,000 steps"],
  ["Splats", "127,565, after trimming"],
]

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

export default function CastleSplat() {
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
        .then(({ startViewer }) => startViewer(canvas, SPLAT_URL, { playing: playingAtStart.current }))
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
  }, [attempt])

  useEffect(() => {
    viewerRef.current?.setPlaying(playing)
  }, [playing, phase])

  const live = phase === "ready"

  return (
    <Layout>
      <section className="k-stack k-stack--4">
        {/* Kai's own words, as she approved them (teable:coilyco/eco-app#8572). */}
        <h1 className="k-display">DRAFT CYCLE 14 CASTLE</h1>
        <p className="castle-sub">
          v1 proof of concept, Gaussian splat of the cycle 14 french castle, pending v2 high quality re-capture
        </p>
      </section>

      <section className="k-stack k-stack--4 castle" aria-labelledby="castle-view">
        <h2 className="k-h2" id="castle-view">
          Look around
        </h2>
        <div className="castle-stage" data-phase={phase}>
          <canvas
            ref={canvasRef}
            className={phase === "failed" || phase === "unsupported" ? "castle-canvas castle-canvas--off" : "castle-canvas"}
            // The canvas draws pixels no assistive tech can read, so it is one
            // labelled image with its controls described beside it.
            role="img"
            aria-label="3D view of the castle draft, a Gaussian splat. The facts below describe the capture."
            aria-describedby="castle-help"
            tabIndex={live ? 0 : -1}
            aria-hidden={!live}
          />
          {phase === "loading" ? (
            <Loading label="Loading the castle draft, 4.4 MB…" testid="castle-loading" />
          ) : null}
          {phase === "unsupported" ? (
            <p className="castle-note" role="status" data-testid="castle-unsupported">
              This browser cannot draw 3D with WebGL 2, so the castle draft cannot show here. A recent desktop
              browser can.
            </p>
          ) : null}
          {phase === "failed" ? (
            <div className="castle-note k-stack k-stack--3" role="alert" data-testid="castle-failed">
              <p>The castle draft did not load. The file may be unavailable right now.</p>
              <div className="k-btn-row">
                <button type="button" className="k-btn k-btn--quiet" onClick={() => {
                    setPhase("loading")
                    setAttempt((n) => n + 1)
                  }}>
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
        <p className="k-hint" id="castle-help">
          Drag to orbit, scroll or pinch to zoom. With the view focused, the arrow keys turn it and plus and minus
          zoom.
        </p>
      </section>

      <section className="k-stack k-stack--3" aria-labelledby="castle-facts">
        <h2 className="k-h2" id="castle-facts">
          About the capture
        </h2>
        <dl className="k-deflist">
          {FACTS.map(([term, detail]) => (
            <div key={term}>
              <dt>{term}</dt>
              <dd>{detail}</dd>
            </div>
          ))}
        </dl>
      </section>
    </Layout>
  )
}
