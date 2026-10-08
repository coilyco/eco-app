import hero from "../assets/biodrive-v2-hero.jpg"
import SplatPage, { type SplatConfig } from "./SplatPage"

// Scuba Steve's BioDrive station, cycle 14. The title and caption are Kai's, from
// COI-2368, and the facts are from the capture's run.json in eco-ops. Everything but
// the numbers is SplatPage, docs/frontend/biodrive-splat.md.
export const SPLAT_URL = "https://files.coilysiren.me/eco/cycle-14/biodrive-v2.sog"

export const BIODRIVE: SplatConfig = {
  slug: "biodrive",
  subject: "the BioDrive station",
  title: "BioDrive station",
  caption: "Scuba Steve's BioDrive station in Phantom Springs, cycle 14.",
  hero,
  heroAlt: "Scuba Steve's BioDrive station in Phantom Springs, a still from the 3D capture.",
  splatUrl: SPLAT_URL,
  size: "14.8 MB",
  facts: [
    ["Frames", "297 captured in 21 minutes, 287 used"],
    ["Reconstruction", "COLMAP 4.2.1, 287 of 287 registered at 0.67 px"],
    ["Training", "Brush v0.3.0, 30,000 steps at 3440 px"],
    ["Quality", "27.75 dB PSNR over 20 held-out frames, before trimming"],
    ["Splats", "881,854, after trimming"],
    ["Footprint", "About 36 by 54 blocks, with the shop's interior"],
  ],
  // About 36 by 54 blocks, a third of the castle's 150, so the zoom range and the far
  // clip scale down with it. The start camera is where the still was taken.
  view: {
    name: "biodrive",
    target: [0, 3, 0],
    reference: [-34, 24, -34],
    fov: 50,
    distanceRange: [6, 120],
    farClip: 600,
  },
}

export default function BioDriveSplat() {
  return <SplatPage config={BIODRIVE} />
}
