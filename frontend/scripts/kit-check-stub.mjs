// A deterministic stand-in for the data plane, in front of the fused service
// (COI-2617). It answers the /preview/*.json routes the price pages read from
// the committed, trimmed production payloads in src/test/fixtures/norms (the
// same files and route map priceNormsRender.test.tsx renders) and forwards
// everything else, the shell included, to the real service.
//
//   node scripts/kit-check-stub.mjs --listen 4011 --to http://127.0.0.1:4010
//
// KIT_CHECK_CONTROL=bloat pads every payload with 1.5 MiB of incompressible
// text, the regression the transfer budget has to catch (negative control).
// Walkthrough: docs/frontend/kit-checks.md.

import { randomBytes } from "node:crypto"
import { readFileSync } from "node:fs"
import http from "node:http"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { gzipSync } from "node:zlib"

const HERE = path.dirname(fileURLToPath(import.meta.url))
const NORMS = path.join(HERE, "..", "src", "test", "fixtures", "norms")
const args = process.argv.slice(2)
const flag = (name) => args[args.indexOf(name) + 1]
const listen = Number(flag("--listen"))
const upstream = new URL(flag("--to"))
const bloat = process.env.KIT_CHECK_CONTROL === "bloat"

const { payloads } = JSON.parse(readFileSync(path.join(NORMS, "serve.json"), "utf8"))
// Production always sends `warnings` and the pages read it, the fixtures drop it.
const bodies = Object.fromEntries(
  Object.entries(payloads).map(([route, file]) => {
    const payload = { warnings: [], ...JSON.parse(readFileSync(path.join(NORMS, file), "utf8")) }
    if (bloat) payload.kitCheckPad = randomBytes(1.5 * 1024 * 1024 * 0.75).toString("base64")
    return [route, Buffer.from(JSON.stringify(payload))]
  }),
)

http
  .createServer((req, res) => {
    const route = new URL(req.url, "http://stub").pathname
    const body = bodies[route]
    if (body) {
      const zip = /\bgzip\b/.test(req.headers["accept-encoding"] ?? "")
      res.writeHead(200, { "content-type": "application/json", ...(zip && { "content-encoding": "gzip" }) })
      res.end(zip ? gzipSync(body) : body)
      return
    }
    const forward = http.request(
      { host: upstream.hostname, port: upstream.port, path: req.url, method: req.method, headers: req.headers },
      (r) => {
        res.writeHead(r.statusCode ?? 502, r.headers)
        r.pipe(res)
      },
    )
    forward.on("error", (e) => res.writeHead(502).end(String(e)))
    req.pipe(forward)
  })
  .listen(listen, "127.0.0.1", () => console.log(`kit-check stub on ${listen}, ${Object.keys(bodies).length} payloads, forwarding to ${upstream.origin}`))
