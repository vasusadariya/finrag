package parse

import (
	"bytes"
	"encoding/json"
	"flag"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

var update = flag.Bool("update", false, "rewrite golden files")

// Real-filing tests need the pinned sample 10-Ks: scripts/fetch-sample-filings.sh
// (or set FINRAG_FILINGS_DIR). They are skipped when the HTML is absent.
func filingsDir() string {
	if d := os.Getenv("FINRAG_FILINGS_DIR"); d != "" {
		return d
	}
	return filepath.Join("..", "..", "testdata", "filings")
}

type goldenSection struct {
	Item      string `json:"item"`
	Synthetic bool   `json:"synthetic,omitempty"`
	StartPage int    `json:"start_page"`
	EndPage   int    `json:"end_page"`
	Blocks    int    `json:"blocks"`
	Chars     int    `json:"chars"`
}

var tickers = []string{"AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "JPM", "XOM"}

func parseFiling(t *testing.T, ticker string) (Document, Meta) {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(filingsDir(), ticker+".htm"))
	if err != nil {
		t.Skipf("sample filing %s not present (run scripts/fetch-sample-filings.sh)", ticker)
	}
	blocks, err := ExtractBlocks(bytes.NewReader(raw))
	if err != nil {
		t.Fatal(err)
	}
	return Split(blocks), ExtractMeta(raw)
}

func TestGoldenSections(t *testing.T) {
	for _, tk := range tickers {
		t.Run(tk, func(t *testing.T) {
			doc, _ := parseFiling(t, tk)
			var got []goldenSection
			for _, s := range doc.Sections {
				got = append(got, goldenSection{s.Item, s.Synthetic, s.StartPage(), s.EndPage(), len(s.Blocks), s.chars()})
			}
			path := filepath.Join("testdata", tk+".json")
			enc, _ := json.MarshalIndent(got, "", "  ")
			enc = append(enc, '\n')
			if *update {
				if err := os.MkdirAll("testdata", 0o755); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(path, enc, 0o644); err != nil {
					t.Fatal(err)
				}
				return
			}
			want, err := os.ReadFile(path)
			if err != nil {
				t.Fatalf("missing golden %s (run with -update)", path)
			}
			if !bytes.Equal(want, enc) {
				t.Errorf("section boundaries changed for %s; review with `finrag-ingest parse` and re-run with -update\n got:\n%s", tk, enc)
			}
		})
	}
}

func total(doc Document, item string) int {
	n := 0
	for _, s := range doc.Item(item) {
		n += s.chars()
	}
	return n
}

// Independent invariants: these must hold regardless of how the golden files were produced.
func TestRealFilingInvariants(t *testing.T) {
	// JPM/XOM Item 7A is only a short pointer into the annual-report section, hence the low floor.
	minChars := map[string]int{"1": 5000, "1A": 30000, "7": 15000, "7A": 200, "8": 30000}
	for _, tk := range tickers {
		t.Run(tk, func(t *testing.T) {
			doc, meta := parseFiling(t, tk)
			for item, min := range minChars {
				if got := total(doc, item); got < min {
					t.Errorf("Item %s has %d chars, want >= %d (warnings: %v)", item, got, min, doc.Warnings)
				}
			}
			prevEnd := 0
			for _, s := range doc.Sections {
				if s.Synthetic {
					continue
				}
				if s.StartPage() < prevEnd {
					t.Errorf("Item %s starts on page %d before previous section ended (%d)", s.Item, s.StartPage(), prevEnd)
				}
				prevEnd = s.EndPage()
			}
			if meta.FiscalYear < 2024 || meta.Registrant == "" {
				t.Errorf("meta = %+v", meta)
			}
		})
	}
}

// Hand-picked facts, read off the filings, that must land in the right Item.
func TestSpotChecks(t *testing.T) {
	checks := []struct{ ticker, item, contains string }{
		{"AAPL", "2", "Cupertino"}, {"MSFT", "2", "Redmond"}, {"GOOGL", "2", "Mountain View"},
		{"AMZN", "2", "facilities"}, {"NVDA", "2", "Santa Clara"}, {"META", "2", "Menlo Park"},
		{"JPM", "2", "270 Park"}, {"XOM", "1", "New Jersey"},
		{"AAPL", "8", "total assets"}, {"MSFT", "8", "total assets"}, {"GOOGL", "8", "total assets"},
		{"AMZN", "8", "total assets"}, {"NVDA", "8", "total assets"}, {"META", "8", "total assets"},
		{"JPM", "8", "total assets"}, {"XOM", "8", "total assets"},
		{"AAPL", "1A", "risk"}, {"JPM", "7", "net income"}, {"XOM", "7", "upstream"}, {"NVDA", "7", "revenue"},
	}
	cache := map[string]Document{}
	for _, c := range checks {
		doc, ok := cache[c.ticker]
		if !ok {
			doc, _ = parseFiling(t, c.ticker)
			cache[c.ticker] = doc
		}
		found := false
		for _, s := range doc.Item(c.item) {
			if strings.Contains(strings.ToLower(s.Text()), strings.ToLower(c.contains)) {
				found = true
			}
		}
		if !found {
			t.Errorf("%s Item %s should mention %q", c.ticker, c.item, c.contains)
		}
	}
}
