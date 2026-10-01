// @vitest-environment node
import { existsSync, readFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { describe, expect, it } from "vitest"
import { DEFAULT_IMAGE, HEIGHT, WIDTH, jobs, render, seeded } from "./og-cards.mjs"

const HERE = path.dirname(fileURLToPath(import.meta.url))
const manifest = JSON.parse(readFileSync(path.join(HERE, "..", "..", "data", "spa_routes.json"), "utf8"))

const dimensions = (png) => ({ width: png.readUInt32BE(16), height: png.readUInt32BE(20) })
const draw = (n, rand) => Array.from({ length: n }, rand)

describe("seeded", () => {
  it("gives one path the same art every time, and another path different art", () => {
    expect(draw(8, seeded("/items"))).toEqual(draw(8, seeded("/items")))
    expect(draw(8, seeded("/items"))).not.toEqual(draw(8, seeded("/map")))
  })
})

describe("jobs", () => {
  it("draws a card for every route that has words, so none falls back to the default unseen", () => {
    const drawn = new Set(jobs().map((job) => job.image))
    for (const route of manifest.routes.filter((r) => r.title)) {
      expect(route.image, `${route.path} has a title and no image`).toBeTruthy()
      expect(drawn.has(route.image), route.path).toBe(true)
    }
  })

  it("gives each card its own file, and the default one a card of its own", () => {
    const images = jobs().map((job) => job.image)
    expect(new Set(images).size).toBe(images.length)
    expect(images).toContain(DEFAULT_IMAGE)
  })

  it("keeps each image under /og/ as a png, which is what the shell and the server name", () => {
    for (const { image } of jobs()) expect(image).toMatch(/^\/og\/[a-z0-9-]+\.png$/)
  })

  it("finds the art every route names", () => {
    for (const route of manifest.routes.filter((r) => r.art)) {
      expect(existsSync(path.join(HERE, "..", "src", "assets", "og-art", route.art)), route.path).toBe(true)
    }
  })
})

describe("render", () => {
  it("draws a 1200x630 png for the default card", async () => {
    const png = await render(jobs().find((job) => job.image === DEFAULT_IMAGE))
    expect(png.subarray(1, 4).toString()).toBe("PNG")
    expect(dimensions(png)).toEqual({ width: WIDTH, height: HEIGHT })
  })

  it("survives a title far longer than any page has, rather than overflowing the card", async () => {
    const png = await render({ title: "A ".repeat(120), description: "word ".repeat(200), url: "eco-app.coilysiren.me/x", seed: "/x" })
    expect(dimensions(png)).toEqual({ width: WIDTH, height: HEIGHT })
  })

  it("draws a route that names art", async () => {
    const withArt = jobs().find((job) => job.photo)
    expect(withArt, "no route names art").toBeTruthy()
    expect(dimensions(await render(withArt))).toEqual({ width: WIDTH, height: HEIGHT })
  })
})
