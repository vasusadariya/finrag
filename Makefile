.PHONY: migrate-up db-local db-local-stop lint lint-py lint-go test test-py test-go fmt up down ingest eval

lint: lint-py lint-go

lint-py:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy

lint-go:
	cd ingest-go && golangci-lint run ./...
	cd sqlguard-go && golangci-lint run ./...

test: test-py test-go

test-py:
	cd backend && uv run pytest

test-go:
	cd ingest-go && go test ./...
	cd sqlguard-go && go test ./...

fmt:
	cd backend && uv run ruff format . && uv run ruff check --fix .
	gofmt -w ingest-go sqlguard-go

up:
	docker compose up -d

down:
	docker compose down

ingest:
	@echo "not implemented until step 1.11"; exit 1

eval:
	@echo "not implemented until step 3.13"; exit 1

db-local:
	scripts/dev-postgres.sh start

db-local-stop:
	scripts/dev-postgres.sh stop

migrate-up:
	cd ingest-go && go run -tags "postgres file" github.com/golang-migrate/migrate/v4/cmd/migrate -path ../db/migrations -database "$${DATABASE_URL:-postgres://finrag:finrag@localhost:5432/finrag?sslmode=disable}" up
