// The one place the PlayCanvas engine is touched, imported on demand so only the
// splat routes pay for it. The page owns every state a person can see (loading,
// failed, unsupported); this module owns the camera and the input.
// docs/frontend/castle-splat.md.

export interface SplatViewer {
  destroy(): void
  setPlaying(playing: boolean): void
  reset(): void
  turn(yaw: number, pitch: number): void
  zoom(factor: number): void
}

// A splat's frame is in Eco blocks, so every number here is blocks, in the y-up frame
// the page draws in. `reference` is the reference viewer's start camera, `target` what
// it looks at, and the clip planes and zoom range scale with the size of the build.
export interface ViewSpec {
  name: string
  target: [number, number, number]
  reference: [number, number, number]
  fov: number
  distanceRange: [number, number]
  farClip: number
  /**
   * A capture arrives a few degrees off true up, since the reconstruction picks its own
   * frame. This turns it about a horizontal `axis` through the origin by `degrees`, so a
   * vertical build reads vertical. Measured per capture, by triangulating a tower's axis.
   */
  level?: { axis: [number, number, number]; degrees: number }
}

// The castle is about 150 blocks across. It opens on its tallest tower (COI-2248), the
// clock tower, centred and 62 blocks away at a 3 degree rise, from the south-south-east.
// The tower leaned 4.8 degrees in the capture, so `level` stands it up.
export const CASTLE_VIEW: ViewSpec = {
  name: "castle",
  target: [-21.9, 27, -32.5],
  reference: [9.1, 30.2, -86.1],
  fov: 50,
  distanceRange: [15, 400],
  farClip: 2000,
  level: { axis: [0.7, 0, -0.71], degrees: 4.84 },
}

const ORBIT_SPEED = 0.15 // radians per second
const PITCH_LIMIT = 1.45

const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n))

export async function startViewer(
  canvas: HTMLCanvasElement,
  url: string,
  options: { playing: boolean; view?: ViewSpec },
): Promise<SplatViewer> {
  const view = options.view ?? CASTLE_VIEW
  const { target: TARGET, fov: FOV, distanceRange: DISTANCE_RANGE } = view
  const offset = view.reference.map((value, axis) => value - TARGET[axis]!)
  const reach = Math.hypot(...offset)
  const START = { yaw: Math.atan2(offset[0]!, offset[2]!), pitch: Math.asin(offset[1]! / reach), distance: reach }
  const pc = await import("playcanvas")
  const app = new pc.Application(canvas, {
    graphicsDeviceOptions: { antialias: false, alpha: false, powerPreference: "high-performance" },
  })
  // CSS sizes the canvas. AUTO resolution makes the engine follow that size
  // every frame, which fill mode alone does not (it only sets the style).
  app.setCanvasFillMode(pc.FILLMODE_NONE)
  app.setCanvasResolution(pc.RESOLUTION_AUTO)
  app.graphicsDevice.maxPixelRatio = Math.min(window.devicePixelRatio || 1, 2)

  // The hero still's flat sky (#8B9EBF), so the handoff from still to live view does not flash.
  const SKY = new pc.Color(0x8b / 255, 0x9e / 255, 0xbf / 255)
  const camera = new pc.Entity("camera")
  camera.addComponent("camera", { clearColor: SKY, fov: FOV, nearClip: 0.1, farClip: view.farClip })
  app.root.addChild(camera)

  const state = { ...START, playing: options.playing, dragging: false }
  const place = () => {
    const flat = Math.cos(state.pitch)
    camera.setPosition(
      TARGET[0] + state.distance * flat * Math.sin(state.yaw),
      TARGET[1] + state.distance * Math.sin(state.pitch),
      TARGET[2] + state.distance * flat * Math.cos(state.yaw),
    )
    camera.lookAt(...TARGET)
  }
  place()

  const cleanup: Array<() => void> = []
  const destroy = () => {
    cleanup.forEach((undo) => undo())
    app.destroy()
  }

  try {
    const asset = new pc.Asset(view.name, "gsplat", { url })
    app.assets.add(asset)
    await new Promise<void>((resolve, reject) => {
      asset.once("load", () => resolve())
      asset.once("error", (message: unknown) => reject(new Error(String(message))))
      app.assets.load(asset)
    })
    const splat = new pc.Entity(view.name)
    // 3DGS is y-down, so a capture arrives upside down without this turn. v2 is y-down too.
    splat.setLocalEulerAngles(0, 0, 180)
    if (view.level) {
      const [x, y, z] = view.level.axis
      // The tilt is about a world axis, so it goes on after the flip.
      splat.setLocalRotation(new pc.Quat().setFromAxisAngle(new pc.Vec3(x, y, z).normalize(), view.level.degrees).mul(splat.getLocalRotation()))
    }
    splat.addComponent("gsplat", { asset })
    app.root.addChild(splat)
  } catch (error) {
    destroy()
    throw error
  }

  app.on("update", (dt: number) => {
    if (state.playing && !state.dragging) state.yaw += ORBIT_SPEED * dt
    place()
  })
  app.start()

  const pointers = new Map<number, { x: number; y: number }>()
  let pinch = 0
  const spread = () => {
    const [a, b] = [...pointers.values()]
    return Math.hypot(a.x - b.x, a.y - b.y)
  }
  const on = <K extends keyof HTMLElementEventMap>(type: K, handler: (event: HTMLElementEventMap[K]) => void, opts?: AddEventListenerOptions) => {
    canvas.addEventListener(type, handler, opts)
    cleanup.push(() => canvas.removeEventListener(type, handler))
  }
  const turn = (yaw: number, pitch: number) => {
    state.yaw += yaw
    state.pitch = clamp(state.pitch + pitch, -PITCH_LIMIT, PITCH_LIMIT)
  }
  const zoom = (factor: number) => {
    state.distance = clamp(state.distance * factor, ...DISTANCE_RANGE)
  }

  on("pointerdown", (event) => {
    canvas.setPointerCapture(event.pointerId)
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY })
    state.dragging = true
    if (pointers.size === 2) pinch = spread()
  })
  on("pointermove", (event) => {
    const last = pointers.get(event.pointerId)
    if (!last) return
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY })
    if (pointers.size === 2) {
      const now = spread()
      if (pinch > 0 && now > 0) zoom(pinch / now)
      pinch = now
    } else {
      turn(-(event.clientX - last.x) * 0.005, (event.clientY - last.y) * 0.005)
    }
  })
  const release = (event: PointerEvent) => {
    pointers.delete(event.pointerId)
    if (pointers.size === 0) state.dragging = false
  }
  on("pointerup", release)
  on("pointercancel", release)
  on("wheel", (event) => {
    event.preventDefault()
    zoom(Math.exp(event.deltaY * 0.001))
  }, { passive: false })
  on("keydown", (event) => {
    const step = 0.12
    const moves: Record<string, () => void> = {
      ArrowLeft: () => turn(-step, 0),
      ArrowRight: () => turn(step, 0),
      ArrowUp: () => turn(0, step),
      ArrowDown: () => turn(0, -step),
      "+": () => zoom(0.9),
      "=": () => zoom(0.9),
      "-": () => zoom(1.1),
    }
    const move = moves[event.key]
    if (!move) return
    event.preventDefault()
    move()
  })

  return {
    destroy,
    setPlaying: (playing) => {
      state.playing = playing
    },
    reset: () => Object.assign(state, START),
    turn,
    zoom,
  }
}
