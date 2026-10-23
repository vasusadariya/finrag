// Package dbtest gives tests a throwaway, fully migrated Postgres database.
package dbtest

import (
	"context"
	"database/sql"
	"fmt"
	"net/url"
	"os"
	"testing"
	"time"

	"github.com/golang-migrate/migrate/v4"
	// database/source drivers for golang-migrate and the SQL driver
	_ "github.com/golang-migrate/migrate/v4/database/postgres"
	_ "github.com/golang-migrate/migrate/v4/source/file"
	_ "github.com/jackc/pgx/v5/stdlib"
)

// MigrationsDir is relative to a package directory two levels below ingest-go (internal/<pkg>).
const MigrationsDir = "file://../../../db/migrations"

func adminURL() string {
	if v := os.Getenv("TEST_DATABASE_URL"); v != "" {
		return v
	}
	return "postgres://finrag:finrag@localhost:5432/finrag?sslmode=disable"
}

// Scratch creates an empty database and returns its URL and handle. Without a reachable Postgres the test is
// skipped, or failed when FINRAG_REQUIRE_DB is set (use that in CI).
func Scratch(t *testing.T) (dbURL string, db *sql.DB) {
	t.Helper()
	unavailable := func(err error) {
		if os.Getenv("FINRAG_REQUIRE_DB") != "" {
			t.Fatalf("postgres required but unavailable: %v", err)
		}
		t.Skipf("postgres unavailable (set FINRAG_REQUIRE_DB=1 to fail instead): %v", err)
	}
	admin, err := sql.Open("pgx", adminURL())
	if err != nil {
		unavailable(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if err := admin.PingContext(ctx); err != nil {
		unavailable(err)
	}
	name := fmt.Sprintf("finrag_test_%d", time.Now().UnixNano())
	if _, err := admin.Exec("CREATE DATABASE " + name); err != nil {
		t.Fatal(err)
	}
	u, _ := url.Parse(adminURL())
	u.Path = "/" + name
	t.Cleanup(func() {
		_, _ = admin.Exec("DROP DATABASE " + name + " WITH (FORCE)")
		_ = admin.Close()
	})
	db, err = sql.Open("pgx", u.String())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = db.Close() })
	return u.String(), db
}

// Migrated is Scratch plus all migrations applied.
func Migrated(t *testing.T) *sql.DB {
	t.Helper()
	dbURL, db := Scratch(t)
	m, err := migrate.New(MigrationsDir, dbURL)
	if err != nil {
		t.Fatal(err)
	}
	if err := m.Up(); err != nil {
		t.Fatal(err)
	}
	return db
}
