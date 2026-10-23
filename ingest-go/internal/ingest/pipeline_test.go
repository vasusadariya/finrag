package ingest

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"

	"github.com/vasusadariya/finrag/ingest-go/internal/chunk"
	"github.com/vasusadariya/finrag/ingest-go/internal/dbtest"
	"github.com/vasusadariya/finrag/ingest-go/internal/edgar"
	"github.com/vasusadariya/finrag/ingest-go/internal/load"
)

var acme = Company{Ticker: "ACME", CIK: "99", Name: "Acme Corp"}

func filingHTML(fy int, variant string) string {
	var sb strings.Builder
	fmt.Fprintf(&sb, `<html><body><ix:header><ix:hidden><ix:nonNumeric name="dei:DocumentFiscalYearFocus">%d</ix:nonNumeric>
<ix:nonNumeric name="dei:EntityRegistrantName">Acme Corporation</ix:nonNumeric></ix:hidden></ix:header>
<div>ANNUAL REPORT cover page</div><div style="page-break-after:always"></div>`, fy)
	items := []struct{ item, title string }{
		{"1", "Business"}, {"1A", "Risk Factors"}, {"7", "Management's Discussion and Analysis"},
		{"7A", "Quantitative and Qualitative Disclosures About Market Risk"}, {"8", "Financial Statements and Supplementary Data"},
	}
	for _, it := range items {
		fmt.Fprintf(&sb, `<div>Item %s. %s</div>`, it.item, it.title)
		for i := 0; i < 400; i++ {
			fmt.Fprintf(&sb, `<p>FY%d %s %s paragraph %d explains something specific about operations and results in plain words.</p>`, fy, variant, it.item, i)
		}
		sb.WriteString(`<div style="page-break-after:always"></div>`)
	}
	sb.WriteString(`</body></html>`)
	return sb.String()
}

const submissions = `{"cik":"0000000099","filings":{"recent":{
"accessionNumber":["0000000099-25-000001","0000000099-25-000050","0000000099-24-000001"],
"filingDate":["2025-11-01","2025-08-01","2024-11-01"],
"reportDate":["2025-09-27","2025-06-28","2024-09-28"],
"form":["10-K","10-Q","10-K"],
"primaryDocument":["acme-2025.htm","acme-q3.htm","acme-2024.htm"]}}}`

const companyFacts = `{"cik":99,"entityName":"Acme","facts":{"us-gaap":{
"NetIncomeLoss":{"units":{"USD":[
 {"start":"2023-10-01","end":"2024-09-28","val":100,"accn":"0000000099-24-000001","fy":2024,"fp":"FY","form":"10-K","filed":"2024-11-01"},
 {"start":"2024-09-29","end":"2025-09-27","val":120,"accn":"0000000099-25-000001","fy":2025,"fp":"FY","form":"10-K","filed":"2025-11-01"}]}},
"Assets":{"units":{"USD":[
 {"end":"2024-09-28","val":900,"accn":"0000000099-24-000001","fy":2024,"fp":"FY","form":"10-K","filed":"2024-11-01"},
 {"end":"2025-09-27","val":1000,"accn":"0000000099-25-000001","fy":2025,"fp":"FY","form":"10-K","filed":"2025-11-01"}]}}}}}`

// fakeEDGAR serves the submissions index, two filings and companyfacts. variant2025 can be changed between runs.
type fakeEDGAR struct {
	srv         *httptest.Server
	mu          sync.Mutex
	variant2025 string
	shrink      atomic.Bool
}

func newFakeEDGAR(t *testing.T) *fakeEDGAR {
	f := &fakeEDGAR{variant2025: "v1"}
	f.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		v := f.variant2025
		f.mu.Unlock()
		switch {
		case r.URL.Path == "/submissions/CIK0000000099.json":
			_, _ = w.Write([]byte(submissions))
		case r.URL.Path == "/api/xbrl/companyfacts/CIK0000000099.json":
			_, _ = w.Write([]byte(companyFacts))
		case strings.HasSuffix(r.URL.Path, "/acme-2025.htm"):
			if f.shrink.Load() {
				_, _ = w.Write([]byte("<html><body><div>Item 8. Financial Statements and Supplementary Data</div><div>See annual report.</div></body></html>"))
				return
			}
			_, _ = w.Write([]byte(filingHTML(2025, v)))
		case strings.HasSuffix(r.URL.Path, "/acme-2024.htm"):
			_, _ = w.Write([]byte(filingHTML(2024, "v1")))
		default:
			http.NotFound(w, r)
		}
	}))
	t.Cleanup(f.srv.Close)
	return f
}

func (f *fakeEDGAR) setVariant(v string) {
	f.mu.Lock()
	f.variant2025 = v
	f.mu.Unlock()
}

type fakeEmbedder struct {
	calls atomic.Int32
	fail  atomic.Bool
}

func (e *fakeEmbedder) Embed(_ context.Context, chunks []chunk.Chunk) (map[string][]float32, error) {
	e.calls.Add(1)
	if e.fail.Load() {
		return nil, errors.New("embedder down")
	}
	out := map[string][]float32{}
	for i, c := range chunks {
		v := make([]float32, load.EmbeddingDim)
		v[i%load.EmbeddingDim] = 1
		out[c.Hash()] = v
	}
	return out, nil
}

func setup(t *testing.T) (Pipeline, *fakeEDGAR, *fakeEmbedder) {
	t.Helper()
	db := dbtest.Migrated(t)
	fe := newFakeEDGAR(t)
	client, err := edgar.New(edgar.Config{UserAgent: "test test@example.com", RatePerSec: 1000, MaxRetries: 1})
	if err != nil {
		t.Fatal(err)
	}
	emb := &fakeEmbedder{}
	return Pipeline{EDGAR: client, Endpoints: edgar.Endpoints{Data: fe.srv.URL, Archives: fe.srv.URL + "/Archives"},
		Loader: load.Loader{DB: db}, Embedder: emb, Years: 3, Logf: t.Logf}, fe, emb
}

func dbCount(t *testing.T, p Pipeline, q string) int {
	t.Helper()
	var n int
	if err := p.Loader.DB.QueryRow(q).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}

func TestRunCompanyLoadsEverything(t *testing.T) {
	p, _, emb := setup(t)
	rep, err := p.RunCompany(context.Background(), acme)
	if err != nil {
		t.Fatal(err)
	}
	if len(rep.Filings) != 2 {
		t.Fatalf("filings = %d (10-Q must be ignored)", len(rep.Filings))
	}
	total := 0
	for _, f := range rep.Filings {
		if f.Skipped || f.Chunks == 0 {
			t.Errorf("unexpected report %+v", f)
		}
		total += f.Chunks
	}
	if got := dbCount(t, p, "SELECT count(*) FROM chunks"); got != total {
		t.Errorf("chunks in db = %d, reported %d", got, total)
	}
	if dbCount(t, p, "SELECT count(*) FROM filings") != 2 || dbCount(t, p, "SELECT count(*) FROM companies") != 1 {
		t.Error("filings/companies count wrong")
	}
	if rep.Facts != 4 || dbCount(t, p, "SELECT count(*) FROM xbrl_facts") != 4 {
		t.Errorf("facts = %d", rep.Facts)
	}
	if dbCount(t, p, "SELECT count(*) FROM xbrl_facts WHERE filing_id IS NULL") != 0 {
		t.Error("facts should be linked to their filings")
	}
	// fiscal year comes from the filing's own dei tag, company name from the registrant tag
	if dbCount(t, p, "SELECT count(*) FROM filings WHERE fiscal_year IN (2024, 2025)") != 2 {
		t.Error("fiscal years wrong")
	}
	if dbCount(t, p, "SELECT count(*) FROM chunks WHERE section = 'Item 1A'") == 0 {
		t.Error("no Item 1A chunks")
	}
	if emb.calls.Load() != 2 {
		t.Errorf("embedder called %d times, want once per filing", emb.calls.Load())
	}
}

func TestRerunIsResumableAndSkipsUnchanged(t *testing.T) {
	p, fe, emb := setup(t)
	ctx := context.Background()
	if _, err := p.RunCompany(ctx, acme); err != nil {
		t.Fatal(err)
	}
	before := dbCount(t, p, "SELECT count(*) FROM chunks")

	rep, err := p.RunCompany(ctx, acme)
	if err != nil {
		t.Fatal(err)
	}
	for _, f := range rep.Filings {
		if !f.Skipped {
			t.Errorf("%s should have been skipped", f.Accession)
		}
	}
	if emb.calls.Load() != 2 || dbCount(t, p, "SELECT count(*) FROM chunks") != before {
		t.Errorf("re-run changed things: calls=%d", emb.calls.Load())
	}
	if dbCount(t, p, "SELECT count(*) FROM xbrl_facts") != 4 {
		t.Error("facts duplicated")
	}

	// only the changed filing is re-embedded
	fe.setVariant("v2")
	rep, err = p.RunCompany(ctx, acme)
	if err != nil {
		t.Fatal(err)
	}
	skipped := 0
	for _, f := range rep.Filings {
		if f.Skipped {
			skipped++
		}
	}
	if skipped != 1 || emb.calls.Load() != 3 {
		t.Errorf("skipped=%d calls=%d, want 1 skipped and 3 total embed calls", skipped, emb.calls.Load())
	}
	if dbCount(t, p, "SELECT count(*) FROM chunks WHERE content LIKE '%v1 %' AND fiscal_year = 2025") != 0 {
		t.Error("stale chunks of the old version survived")
	}
}

func TestEmbedderFailureThenRecovery(t *testing.T) {
	p, _, emb := setup(t)
	ctx := context.Background()
	emb.fail.Store(true)
	if _, err := p.RunCompany(ctx, acme); err == nil || !strings.Contains(err.Error(), "embedder down") {
		t.Fatalf("expected embedder error, got %v", err)
	}
	if dbCount(t, p, "SELECT count(*) FROM chunks") != 0 {
		t.Error("partial chunks written despite failure")
	}
	emb.fail.Store(false)
	if _, err := p.RunCompany(ctx, acme); err != nil {
		t.Fatalf("recovery run failed: %v", err)
	}
	if dbCount(t, p, "SELECT count(*) FROM chunks") == 0 {
		t.Error("recovery run loaded nothing")
	}
}

func TestImplausibleParseIsRefusedNotLoaded(t *testing.T) {
	p, fe, emb := setup(t)
	fe.shrink.Store(true) // serve a filing whose Item 8 is a stub
	_, err := p.RunCompany(context.Background(), acme)
	if err == nil || !strings.Contains(err.Error(), "refusing to load") {
		t.Fatalf("expected refusal, got %v", err)
	}
	if dbCount(t, p, "SELECT count(*) FROM chunks") != 0 || emb.calls.Load() != 0 {
		t.Error("a refused filing must not be embedded or loaded")
	}
}

func TestMissingFilingIsReported(t *testing.T) {
	p, _, _ := setup(t)
	_, err := p.RunCompany(context.Background(), Company{Ticker: "NOPE", CIK: "12345", Name: "Nope"})
	if err == nil {
		t.Fatal("expected error for unknown company")
	}
}

func TestRowCounts(t *testing.T) {
	p, _, _ := setup(t)
	if _, err := p.RunCompany(context.Background(), acme); err != nil {
		t.Fatal(err)
	}
	cs, err := RowCounts(context.Background(), p.Loader.DB)
	if err != nil || len(cs) != 1 || cs[0].Ticker != "ACME" || cs[0].Filings != 2 || cs[0].Chunks == 0 || cs[0].Facts != 4 {
		t.Errorf("%+v %v", cs, err)
	}
}

func TestSelect(t *testing.T) {
	all, unk := Select("")
	if len(all) != 8 || unk != nil {
		t.Error("default should be the whole universe")
	}
	got, unk := Select("aapl, jpm ,ZZZ")
	if len(got) != 2 || got[0].Ticker != "AAPL" || got[1].Ticker != "JPM" || len(unk) != 1 || unk[0] != "ZZZ" {
		t.Errorf("%v %v", got, unk)
	}
}

func TestPythonEmbedderRoundTrip(t *testing.T) {
	script := `import sys, json
for line in sys.stdin:
    r = json.loads(line)
    print(json.dumps({"hash": r["hash"], "embedding": [float(len(r["text"]))] + [0.0] * 767}))`
	e := PythonEmbedder{Command: []string{"python3", "-I", "-c", script}}
	f := chunk.Filing{Ticker: "T", Company: "T Corp", FiscalYear: 2025, Form: "10-K"}
	c := chunk.Chunk{Filing: f, Section: "Item 1", Text: "hello world", ContextHeader: "hdr"}
	out, err := e.Embed(context.Background(), []chunk.Chunk{c})
	if err != nil {
		t.Fatal(err)
	}
	v := out[c.Hash()]
	if len(v) != 768 || v[0] != float32(len(c.EmbedText())) {
		t.Errorf("vector = %v...", v[:2])
	}

	bad := PythonEmbedder{Command: []string{"python3", "-I", "-c", "import sys; sys.exit(3)"}}
	if _, err := bad.Embed(context.Background(), []chunk.Chunk{c}); err == nil {
		t.Error("non-zero exit should be an error")
	}
}
