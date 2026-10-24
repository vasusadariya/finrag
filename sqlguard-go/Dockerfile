# SQL guard: Go + libpg_query (cgo). Build context = repository root.
ARG REGISTRY=
FROM ${REGISTRY}golang:1.24-bookworm AS build
WORKDIR /src/sqlguard-go
COPY sqlguard-go/go.mod sqlguard-go/go.sum ./
RUN go mod download
COPY sqlguard-go/ ./
RUN CGO_ENABLED=1 go build -trimpath -ldflags="-s -w" -o /out/sqlguard ./cmd/sqlguard

FROM ${REGISTRY}debian:bookworm-slim
RUN useradd --system --uid 10001 sqlguard
COPY --from=build /out/sqlguard /usr/local/bin/sqlguard
COPY db/sql_policy.json /etc/sqlguard/sql_policy.json
ENV SQLGUARD_ADDR=0.0.0.0:8081 SQLGUARD_POLICY=/etc/sqlguard/sql_policy.json
USER sqlguard
EXPOSE 8081
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD ["/usr/local/bin/sqlguard", "-healthcheck"]
ENTRYPOINT ["/usr/local/bin/sqlguard"]
