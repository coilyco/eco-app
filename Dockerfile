ARG AOS_IMAGE=forgejo.coilysiren.me/coilyco-flight-deck/agentic-os:release

FROM ${AOS_IMAGE} AS frontend

WORKDIR /frontend

RUN corepack enable

COPY frontend/package.json frontend/pnpm-lock.yaml /frontend/
RUN pnpm install --frozen-lockfile

COPY frontend/ /frontend/
# The SPA route table is shared with the Python service (robots.txt, sitemap,
# crawl rules), so it lives in data/ rather than under frontend/. This lands it
# where frontend/src/routes.tsx's `../../data/` import resolves. The homepage's
# server brief lives beside it for the same reason (frontend/src/lib/serverBrief.ts).
COPY data/spa_routes.json /data/spa_routes.json
COPY data/server_brief.json /data/server_brief.json
RUN pnpm build

FROM ${AOS_IMAGE} AS mods

ARG MOD_SOURCE_REVISION=dev

WORKDIR /src

COPY mods/ /src/mods/
COPY scripts/mods-gate.sh scripts/mod_packages.py /src/scripts/

RUN sh scripts/mods-gate.sh build-mods
RUN python3 scripts/mod_packages.py package \
    --repo-root /src \
    --output /mod-packages \
    --revision "${MOD_SOURCE_REVISION}"

FROM ${AOS_IMAGE} AS runtime

WORKDIR /app

COPY pyproject.toml uv.lock /app/
RUN uv sync --frozen --no-dev --no-install-project

COPY . /app
RUN uv sync --frozen --no-dev

COPY --from=frontend /frontend/dist /app/frontend/dist
COPY --from=mods /mod-packages /mod-packages

ENV PORT=4000
EXPOSE $PORT

CMD ["sh", "-c", ".venv/bin/uvicorn eco_mcp_app.http_app:app --host 0.0.0.0 --port $PORT"]
