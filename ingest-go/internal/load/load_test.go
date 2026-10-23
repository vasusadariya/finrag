package load

import (
	"context"
	"database/sql"
	"fmt"
	"math"
	"strings"
	"testing"
	"time"

	"github.com/golang-migrate/migrate/v4"
	"github.com/vasusadariya/finrag/ingest-go/internal/chunk"
	"github.com/vasusadariya/finrag/ingest-go/internal/parse"
	"github.com/vasusadariya/finrag/ingest-go/internal/xbrl"
)

func migratedDB(t *testing.T) *sql.DB {
	t.Helper()
	dbURL, db := scratchDB(t)
	m, err := migrate.New(migrationsDir, dbURL)
	if err != nil {
		t.Fatal(err)
	}
	if err := m.Up(); err != nil {
		t.Fatal(err)
	}
	return db
}

// unitVec returns a 768-d vector with a single hot dimension, so cosine distances are predictable.
func unitVec(hot int) []float32 {
	v := make([]float32, EmbeddingDim)
	v[hot] = 1
	return v
}

func makeChunks(n int, text func(i int) string) ([]chunk.Chunk, map[string][]float32) {
	f := chunk.Filing{Ticker: "ACME", Company: "Acme Corp", FiscalYear: 2025, Form: "10-K", Accession: "0000000001-26-000001"}
	var blocks []parse.Block
	for i := 0; i < n; i++ {
		// each block is long enough to land in its own chunk
		var sb strings.Builder
		sb.WriteString(text(i))
		for j := 0; j < 60; j++ {
			fmt.Fprintf(&sb, " Filler sentence %d-%d adds unique padding.", i, j)
		}
		blocks = append(blocks, parse.Block{Text: sb.String(), Page: i + 1})
	}
	doc := parse.Document{Sections: []parse.Section{{Item: "1A", Title: "Risk Factors", Blocks: blocks}}}
	chunks := chunk.Document(f, doc, chunk.Options{})
	emb := map[string][]float32{}
	for i, c := range chunks {
		emb[c.Hash()] = unitVec(i % EmbeddingDim)
	}
	return chunks, emb
}

func setup(t *testing.T, db *sql.DB) (Loader, int, int) {
	t.Helper()
	l := Loader{DB: db}
	ctx := context.Background()
	cid, err := l.UpsertCompany(ctx, Company{CIK: "1", Ticker: "ACME", Name: "Acme Corp"})
	if err != nil {
		t.Fatal(err)
	}
	fid, err := l.UpsertFiling(ctx, Filing{CompanyID: cid, Accession: "0000000001-26-000001", Form: "10-K", FiscalYear: 2025,
		PeriodEnd: time.Date(2025, 12, 31, 0, 0, 0, 0, time.UTC), URL: "https://example.com/f.htm"})
	if err != nil {
		t.Fatal(err)
	}
	return l, cid, fid
}

func count(t *testing.T, db *sql.DB, q string, args ...any) int {
	t.Helper()
	var n int
	if err := db.QueryRow(q, args...).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}

func TestLoadAndReingestNoDuplicates(t *testing.T) {
	db := migratedDB(t)
	l, cid, fid := setup(t, db)
	chunks, emb := makeChunks(6, func(i int) string { return fmt.Sprintf("Passage %d about topic.", i) })
	ctx := context.Background()
	for run := 0; run < 3; run++ {
		if err := l.ReplaceChunks(ctx, cid, fid, chunks, emb); err != nil {
			t.Fatalf("run %d: %v", run, err)
		}
	}
	if got := count(t, db, "SELECT count(*) FROM chunks"); got != len(chunks) {
		t.Errorf("chunks = %d, want %d", got, len(chunks))
	}
	if got := count(t, db, "SELECT count(DISTINCT content_hash) FROM chunks"); got != len(chunks) {
		t.Errorf("distinct hashes = %d", got)
	}
	// metadata made it through
	if got := count(t, db, "SELECT count(*) FROM chunks WHERE section='Item 1A' AND fiscal_year=2025 AND company_id=$1 AND token_count>0 AND page>0", cid); got != len(chunks) {
		t.Errorf("metadata rows = %d", got)
	}
	if got := count(t, db, "SELECT count(*) FROM chunks WHERE embedding IS NULL OR tsv IS NULL"); got != 0 {
		t.Errorf("%d chunks lack embedding/tsv", got)
	}
}

func TestCompanyAndFilingUpsertIdempotent(t *testing.T) {
	db := migratedDB(t)
	l, cid, fid := setup(t, db)
	ctx := context.Background()
	cid2, _ := l.UpsertCompany(ctx, Company{CIK: "1", Ticker: "ACME", Name: "Acme Corporation"})
	fid2, _ := l.UpsertFiling(ctx, Filing{CompanyID: cid, Accession: "0000000001-26-000001", Form: "10-K", FiscalYear: 2026, PeriodEnd: time.Now()})
	if cid2 != cid || fid2 != fid {
		t.Errorf("ids changed: %d/%d %d/%d", cid, cid2, fid, fid2)
	}
	if n := count(t, db, "SELECT count(*) FROM companies"); n != 1 {
		t.Errorf("companies = %d", n)
	}
	var name string
	var fy int
	_ = db.QueryRow("SELECT name FROM companies").Scan(&name)
	_ = db.QueryRow("SELECT fiscal_year FROM filings").Scan(&fy)
	if name != "Acme Corporation" || fy != 2026 {
		t.Errorf("not updated: %q %d", name, fy)
	}
}

func TestReingestWithFewerChunksRemovesStale(t *testing.T) {
	db := migratedDB(t)
	l, cid, fid := setup(t, db)
	ctx := context.Background()
	big, embBig := makeChunks(8, func(i int) string { return fmt.Sprintf("Old passage %d.", i) })
	if err := l.ReplaceChunks(ctx, cid, fid, big, embBig); err != nil {
		t.Fatal(err)
	}
	small, embSmall := makeChunks(3, func(i int) string { return fmt.Sprintf("New passage %d.", i) })
	if err := l.ReplaceChunks(ctx, cid, fid, small, embSmall); err != nil {
		t.Fatal(err)
	}
	if got := count(t, db, "SELECT count(*) FROM chunks"); got != len(small) {
		t.Errorf("chunks = %d, want %d", got, len(small))
	}
	if got := count(t, db, "SELECT count(*) FROM chunks WHERE content LIKE 'Old passage%'"); got != 0 {
		t.Errorf("%d stale chunks survived", got)
	}
}

func TestVectorAndKeywordSearchWork(t *testing.T) {
	db := migratedDB(t)
	l, cid, fid := setup(t, db)
	chunks, emb := makeChunks(5, func(i int) string {
		if i == 3 {
			return "Our headquarters are in Cupertino."
		}
		return fmt.Sprintf("Generic passage %d.", i)
	})
	if err := l.ReplaceChunks(context.Background(), cid, fid, chunks, emb); err != nil {
		t.Fatal(err)
	}
	// each chunk's own vector must retrieve that chunk first
	for _, k := range []int{0, len(chunks) / 2, len(chunks) - 1} {
		var idx int
		if err := db.QueryRow(`SELECT chunk_index FROM chunks ORDER BY embedding <=> $1::vector LIMIT 1`, vectorLiteral(emb[chunks[k].Hash()])).Scan(&idx); err != nil {
			t.Fatal(err)
		}
		if idx != k {
			t.Errorf("nearest chunk = %d, want %d", idx, k)
		}
	}
	// keyword search finds exactly the chunks containing the marker (overlap may put it in two)
	want := map[int]bool{}
	for _, c := range chunks {
		if strings.Contains(c.Text, "Cupertino") {
			want[c.Index] = true
		}
	}
	rows, err := db.Query(`SELECT chunk_index FROM chunks WHERE tsv @@ plainto_tsquery('english','headquarters Cupertino')`)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = rows.Close() }()
	got := map[int]bool{}
	for rows.Next() {
		var i int
		if err := rows.Scan(&i); err != nil {
			t.Fatal(err)
		}
		got[i] = true
	}
	if len(want) == 0 || len(got) != len(want) {
		t.Errorf("keyword hits %v, want %v", got, want)
	}
	for i := range want {
		if !got[i] {
			t.Errorf("missing keyword hit %d", i)
		}
	}
}

func TestRejectsMissingOrWrongSizedEmbeddings(t *testing.T) {
	db := migratedDB(t)
	l, cid, fid := setup(t, db)
	chunks, emb := makeChunks(2, func(i int) string { return fmt.Sprintf("P%d", i) })
	ctx := context.Background()

	missing := map[string][]float32{chunks[0].Hash(): emb[chunks[0].Hash()]}
	if err := l.ReplaceChunks(ctx, cid, fid, chunks, missing); err == nil {
		t.Error("expected error for missing embedding")
	}
	bad := map[string][]float32{chunks[0].Hash(): unitVec(0), chunks[1].Hash(): make([]float32, 10)}
	if err := l.ReplaceChunks(ctx, cid, fid, chunks, bad); err == nil || !strings.Contains(err.Error(), "dims") {
		t.Errorf("expected dimension error, got %v", err)
	}
	if got := count(t, db, "SELECT count(*) FROM chunks"); got != 0 {
		t.Errorf("failed load left %d rows", got)
	}
}

func TestReadEmbeddings(t *testing.T) {
	m, err := ReadEmbeddings(strings.NewReader(`{"hash":"a","embedding":[0.5,-1]}` + "\n\n" + `{"hash":"b","embedding":[1,2]}` + "\n"))
	if err != nil || len(m) != 2 || math.Abs(float64(m["a"][1])+1) > 1e-6 {
		t.Fatalf("%v %v", m, err)
	}
	for _, bad := range []string{`not json`, `{"hash":"","embedding":[1]}`, `{"hash":"a","embedding":[]}`} {
		if _, err := ReadEmbeddings(strings.NewReader(bad)); err == nil {
			t.Errorf("expected error for %q", bad)
		}
	}
}

func TestVectorLiteral(t *testing.T) {
	if got := vectorLiteral([]float32{1, 0.5, -2.25}); got != "[1,0.5,-2.25]" {
		t.Error(got)
	}
}

func sampleFacts() []xbrl.Fact {
	s := time.Date(2024, 9, 29, 0, 0, 0, 0, time.UTC)
	e := time.Date(2025, 9, 27, 0, 0, 0, 0, time.UTC)
	return []xbrl.Fact{
		{Concept: "NetIncomeLoss", Unit: "USD", PeriodStart: &s, PeriodEnd: e, Value: "112010000000", FiscalYear: 2025, FiscalPeriod: "FY", Accession: "0000000001-26-000001"},
		{Concept: "Assets", Unit: "USD", PeriodEnd: e, Value: "359241000000", FiscalYear: 2025, FiscalPeriod: "FY", Accession: "0000000001-26-000001"},
		{Concept: "EarningsPerShareDiluted", Unit: "USD/shares", PeriodStart: &s, PeriodEnd: e, Value: "7.46", FiscalYear: 2025, FiscalPeriod: "FY", Accession: "0000000009-20-000009"},
	}
}

func TestReplaceFactsIdempotent(t *testing.T) {
	db := migratedDB(t)
	l, cid, fid := setup(t, db)
	ids := map[string]int{"0000000001-26-000001": fid}
	for run := 0; run < 3; run++ {
		if err := l.ReplaceFacts(context.Background(), cid, sampleFacts(), ids); err != nil {
			t.Fatal(err)
		}
	}
	if n := count(t, db, "SELECT count(*) FROM xbrl_facts"); n != 3 {
		t.Errorf("facts = %d, want 3 (instant facts have NULL period_start, so only replace-all keeps them unique)", n)
	}
	if n := count(t, db, "SELECT count(*) FROM xbrl_facts WHERE filing_id = $1", fid); n != 2 {
		t.Errorf("linked to filing: %d, want 2", n)
	}
	if n := count(t, db, "SELECT count(*) FROM xbrl_facts WHERE filing_id IS NULL AND accession = '0000000009-20-000009'"); n != 1 {
		t.Errorf("unknown-filing fact should have NULL filing_id")
	}
	var v string
	if err := db.QueryRow(`SELECT value::text FROM xbrl_facts WHERE concept='EarningsPerShareDiluted'`).Scan(&v); err != nil || v != "7.46" {
		t.Errorf("decimal preserved? %q %v", v, err)
	}
	// facts that vanished upstream are removed
	if err := l.ReplaceFacts(context.Background(), cid, sampleFacts()[:1], ids); err != nil {
		t.Fatal(err)
	}
	if n := count(t, db, "SELECT count(*) FROM xbrl_facts"); n != 1 {
		t.Errorf("after shrink: %d", n)
	}
}

func TestReplaceFactsOnlyTouchesOneCompany(t *testing.T) {
	db := migratedDB(t)
	l, cid, fid := setup(t, db)
	other, err := l.UpsertCompany(context.Background(), Company{CIK: "2", Ticker: "OTHR", Name: "Other"})
	if err != nil {
		t.Fatal(err)
	}
	ids := map[string]int{"0000000001-26-000001": fid}
	for _, c := range []int{cid, other} {
		if err := l.ReplaceFacts(context.Background(), c, sampleFacts(), ids); err != nil {
			t.Fatal(err)
		}
	}
	if err := l.ReplaceFacts(context.Background(), cid, nil, ids); err != nil {
		t.Fatal(err)
	}
	if n := count(t, db, "SELECT count(*) FROM xbrl_facts WHERE company_id = $1", other); n != 3 {
		t.Errorf("other company's facts changed: %d", n)
	}
}
