package edgar

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
)

const subFixture = `{"cik":"0000320193","filings":{"recent":{
"accessionNumber":["0000320193-24-000123","0000320193-24-000100","0000320193-23-000106","0000320193-22-000108","0000320193-21-000105"],
"filingDate":["2024-11-01","2024-08-02","2023-11-03","2022-10-28","2021-10-29"],
"reportDate":["2024-09-28","2024-06-29","2023-09-30","2022-09-24","2021-09-25"],
"form":["10-K","10-Q","10-K","10-K","10-K/A"],
"primaryDocument":["aapl-20240928.htm","aapl-20240629.htm","aapl-20230930.htm","aapl-20220924.htm","aapl-20210925.htm"]}}}`

func TestParseTenKs(t *testing.T) {
	fs, err := ParseTenKs([]byte(subFixture))
	if err != nil {
		t.Fatal(err)
	}
	if len(fs) != 3 {
		t.Fatalf("got %d filings, want 3 (10-Q and 10-K/A excluded)", len(fs))
	}
	if fs[0].Accession != "0000320193-24-000123" || fs[0].FiscalYear != 2024 || fs[0].CIK != "320193" {
		t.Errorf("newest filing wrong: %+v", fs[0])
	}
	if fs[2].FiscalYear != 2022 {
		t.Errorf("oldest = %+v", fs[2])
	}
}

func TestParseTenKsRejectsMalformed(t *testing.T) {
	if _, err := ParseTenKs([]byte(`{"cik":"1","filings":{"recent":{"accessionNumber":["a"],"form":[]}}}`)); err == nil {
		t.Error("expected length mismatch error")
	}
	if _, err := ParseTenKs([]byte(`not json`)); err == nil {
		t.Error("expected json error")
	}
}

func TestURLs(t *testing.T) {
	ep := DefaultEndpoints
	if got := ep.SubmissionsURL("320193"); got != "https://data.sec.gov/submissions/CIK0000320193.json" {
		t.Error(got)
	}
	if got := ep.CompanyFactsURL("0000320193"); got != "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json" {
		t.Error(got)
	}
	f := Filing{CIK: "320193", Accession: "0000320193-24-000123", PrimaryDoc: "aapl-20240928.htm"}
	want := "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm"
	if got := ep.DocumentURL(f); got != want {
		t.Error(got)
	}
}

func TestLatestTenKsOverHTTP(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/submissions/CIK0000320193.json" {
			w.WriteHeader(404)
			return
		}
		_, _ = w.Write([]byte(subFixture))
	}))
	defer srv.Close()
	c := newClient(t, Config{})
	fs, err := c.LatestTenKs(context.Background(), Endpoints{Data: srv.URL}, "320193", 2)
	if err != nil || len(fs) != 2 {
		t.Fatalf("%v %v", fs, err)
	}
}

// TestLive hits the real SEC API. Run with: FINRAG_LIVE=1 EDGAR_USER_AGENT="Name email" go test ./internal/edgar -run Live
func TestLive(t *testing.T) {
	ua := os.Getenv("EDGAR_USER_AGENT")
	if os.Getenv("FINRAG_LIVE") == "" || ua == "" {
		t.Skip("set FINRAG_LIVE=1 and EDGAR_USER_AGENT to run")
	}
	c, err := New(Config{UserAgent: ua, CacheDir: t.TempDir()})
	if err != nil {
		t.Fatal(err)
	}
	fs, err := c.LatestTenKs(context.Background(), DefaultEndpoints, "320193", 3)
	if err != nil {
		t.Fatal(err)
	}
	if len(fs) != 3 {
		t.Fatalf("got %d AAPL 10-Ks", len(fs))
	}
	doc, err := c.FetchDocument(context.Background(), DefaultEndpoints, fs[0])
	if err != nil || len(doc) < 100_000 {
		t.Fatalf("document fetch: len=%d err=%v", len(doc), err)
	}
}
