package load

import (
	"context"
	"database/sql"
	"fmt"
	"net/url"
	"os"
	"testing"
	"time"

	"github.com/golang-migrate/migrate/v4"
	_ "github.com/golang-migrate/migrate/v4/database/postgres"
	_ "github.com/golang-migrate/migrate/v4/source/file"
	_ "github.com/jackc/pgx/v5/stdlib"
)

const migrationsDir = "file://../../../db/migrations"

func adminURL() string {
	if v := os.Getenv("TEST_DATABASE_URL"); v != "" {
		return v
	}
	return "postgres://finrag:finrag@localhost:5432/finrag?sslmode=disable"
}

// scratchDB creates an empty database, skipping the test if Postgres is unreachable.
func scratchDB(t *testing.T) (dbURL string, db *sql.DB) {
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

func TestMigrateUpDown(t *testing.T) {
	dbURL, db := scratchDB(t)
	m, err := migrate.New(migrationsDir, dbURL)
	if err != nil {
		t.Fatalf("migrate setup: %v", err)
	}
	if err := m.Up(); err != nil {
		t.Fatalf("up: %v", err)
	}
	for _, tbl := range []string{"companies", "filings", "chunks", "xbrl_facts", "query_traces"} {
		var n int
		if err := db.QueryRow("SELECT count(*) FROM " + tbl).Scan(&n); err != nil {
			t.Errorf("table %s missing: %v", tbl, err)
		}
	}
	for _, idx := range []string{"chunks_embedding_hnsw_idx", "chunks_tsv_gin_idx"} {
		var n int
		if err := db.QueryRow("SELECT count(*) FROM pg_indexes WHERE indexname=$1", idx).Scan(&n); err != nil {
			t.Fatal(err)
		}
		if n != 1 {
			t.Errorf("index %s missing", idx)
		}
	}
	if err := m.Down(); err != nil {
		t.Fatalf("down: %v", err)
	}
	var n int
	if err := db.QueryRow("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='chunks'").Scan(&n); err != nil {
		t.Fatal(err)
	}
	if n != 0 {
		t.Error("chunks still exists after down")
	}
}

func TestReadOnlyRole(t *testing.T) {
	dbURL, db := scratchDB(t)
	m, err := migrate.New(migrationsDir, dbURL)
	if err != nil {
		t.Fatal(err)
	}
	if err := m.Up(); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`INSERT INTO companies (cik, ticker, name) VALUES ('0000320193','AAPL','Apple Inc.')`); err != nil {
		t.Fatal(err)
	}

	u, _ := url.Parse(dbURL)
	u.User = url.UserPassword("finrag_ro", "finrag_ro")
	ro, err := sql.Open("pgx", u.String())
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = ro.Close() }()

	var n int
	for _, tbl := range []string{"companies", "filings", "xbrl_facts"} {
		if err := ro.QueryRow("SELECT count(*) FROM " + tbl).Scan(&n); err != nil {
			t.Errorf("RO should read %s: %v", tbl, err)
		}
	}
	denied := []string{
		`INSERT INTO companies (cik, ticker, name) VALUES ('1','X','X')`,
		`UPDATE companies SET name = 'x'`,
		`DELETE FROM companies`,
		`TRUNCATE companies`,
		`DROP TABLE companies`,
		`CREATE TABLE evil (id int)`,
		`SELECT count(*) FROM chunks`,
		`SELECT count(*) FROM query_traces`,
	}
	for _, q := range denied {
		if _, err := ro.Exec(q); err == nil {
			t.Errorf("RO role was allowed: %s", q)
		}
	}
}
