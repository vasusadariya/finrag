package load

import (
	"database/sql"
	"net/url"
	"testing"

	"github.com/golang-migrate/migrate/v4"
	"github.com/vasusadariya/finrag/ingest-go/internal/dbtest"
)

func TestMigrateUpDown(t *testing.T) {
	dbURL, db := dbtest.Scratch(t)
	m, err := migrate.New(dbtest.MigrationsDir, dbURL)
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
	dbURL, db := dbtest.Scratch(t)
	m, err := migrate.New(dbtest.MigrationsDir, dbURL)
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
