# git is needed once, to fetch Baileys' libsignal dependency; it is not in the final image.
FROM node:20-bookworm-slim AS deps
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

FROM node:20-bookworm-slim
ENV NODE_ENV=production AUTH_DIR=/data/auth INBOUND_FILE=/data/inbound.jsonl
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY package.json index.js server.js policy.js ./
RUN mkdir -p /data && chown -R node:node /data
USER node
CMD ["node", "index.js"]
