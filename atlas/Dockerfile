# --- stage 1: the dashboard -------------------------------------------------
# Built here so the runtime image carries no Node, no node_modules and no
# toolchain — only the static assets FastAPI serves on the mesh.
FROM node:20-slim AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json* ./
RUN npm ci --no-audit --no-fund
# The design tokens are shared with the server-rendered pages, so the CSS the
# bundle imports lives outside ui/.
COPY atlas/api/templates/_tokens.css /atlas/api/templates/_tokens.css
# Air Glass shows the ATLAS Academy products as 3D cards. The store's catalog
# is cross-origin to the dashboard (CSP connect-src 'self'), so the catalog
# and its covers are bundled from here at build time (ui/src/airglass/catalog.ts).
COPY atlas/modules/academy/catalog.json /atlas/modules/academy/catalog.json
COPY atlas/modules/academy/assets/covers/ /atlas/modules/academy/assets/covers/
COPY ui/ ./
RUN npm run build && rm -f dist/assets/*.map


# --- stage 2: the application ----------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# ffmpeg: the "local" render backend (Task 55) stitches Fish Audio narration
# over story cards with it; without it `content.render_backend` reads UNKNOWN
# and `render_video` refuses. About 100 MB.
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates ffmpeg fonts-dejavu-core fonts-noto-core \
      && rm -rf /var/lib/apt/lists/*

# constraints.txt pins every package this resolves, transitive ones included,
# so a rebuild installs the versions the suite ran against rather than
# whatever PyPI serves that day. The floors below stay as documentation; the
# constraints decide. A new dependency needs a line there too, or it floats
# (tests/test_image_pins.py fails until it has one).
COPY pyproject.toml constraints.txt ./
RUN pip install --no-cache-dir -c constraints.txt \
      "fastapi>=0.115" "uvicorn[standard]>=0.32" "asyncpg>=0.30" \
      "httpx>=0.28" "anthropic>=0.40" "openai>=1.55" "redis>=5.2" \
      "pgvector>=0.3" "python-dateutil>=2.9" "playwright>=1.49" \
      "jinja2>=3.1" "argon2-cffi>=23.1" "itsdangerous>=2.2" \
      "python-multipart>=0.0.9" "numpy>=2.0" "scikit-learn>=1.5"

# The fiverr and linkedin collectors drive a real browser: neither service has
# an API, and both are read-only here. Without this every one of their checks
# reports Health.UNKNOWN ("No module named 'playwright'"), which is honest but
# useless. Installed as root into a shared path so the unprivileged runtime
# user can still launch it.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright

# The user is created BEFORE the app is copied so ownership can be set as part
# of the COPY. `chown -R /app` afterwards writes a second copy of every file
# into a new layer — on a tree that includes the built UI that is gigabytes of
# pure duplication, and it is what filled this box's disk.
RUN useradd -m -u 10001 atlas

COPY --chown=atlas:atlas . .
COPY --from=ui --chown=atlas:atlas /ui/dist ./ui/dist

# Never run as root.
USER atlas

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -fsS http://localhost:8080/healthz || exit 1

CMD ["uvicorn", "atlas.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
