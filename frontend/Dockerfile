# Web image: Next.js standalone server. Build context = ./frontend
ARG REGISTRY=
FROM ${REGISTRY}node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM ${REGISTRY}node:22-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# rewrites are compiled into the build, so the API address must be known here (compose network name)
ARG API_INTERNAL_URL=http://api:8000
ENV NEXT_TELEMETRY_DISABLED=1 API_INTERNAL_URL=$API_INTERNAL_URL
RUN npm run build

FROM ${REGISTRY}node:22-alpine
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
RUN addgroup -S web && adduser -S web -G web
COPY --from=build --chown=web:web /app/.next/standalone ./
COPY --from=build --chown=web:web /app/.next/static ./.next/static
USER web
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=4s --retries=5 CMD wget -qO- http://127.0.0.1:3000/ >/dev/null || exit 1
CMD ["node", "server.js"]
