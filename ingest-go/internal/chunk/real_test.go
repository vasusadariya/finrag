package chunk

import (
	"bytes"
	"os"
	"path/filepath"
	"sort"
	"testing"

	"github.com/vasusadariya/finrag/ingest-go/internal/parse"
)

func realChunks(t *testing.T, ticker string) []Chunk {
	t.Helper()
	dir := os.Getenv("FINRAG_FILINGS_DIR")
	if dir == "" {
		dir = filepath.Join("..", "..", "testdata", "filings")
	}
	raw, err := os.ReadFile(filepath.Join(dir, ticker+".htm"))
	if err != nil {
		t.Skipf("sample filing %s not present (run scripts/fetch-sample-filings.sh)", ticker)
	}
	blocks, err := parse.ExtractBlocks(bytes.NewReader(raw))
	if err != nil {
		t.Fatal(err)
	}
	meta := parse.ExtractMeta(raw)
	f := Filing{Ticker: ticker, Company: meta.Registrant, FiscalYear: meta.FiscalYear, Form: "10-K", Accession: "test"}
	return Document(f, parse.Split(blocks), Options{})
}

func TestRealFilingsChunking(t *testing.T) {
	for _, tk := range []string{"AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "JPM", "XOM", "JPM2024"} {
		t.Run(tk, func(t *testing.T) {
			chunks := realChunks(t, tk)
			if len(chunks) < 50 {
				t.Fatalf("only %d chunks", len(chunks))
			}
			sizes := make([]int, len(chunks))
			hashes := map[string]bool{}
			sections := map[string]int{}
			for i, c := range chunks {
				sizes[i] = len(c.Text)
				sections[c.Section]++
				if len(c.Text) > MaxChars || len(c.Text) == 0 {
					t.Fatalf("chunk %d size %d", i, len(c.Text))
				}
				if c.Filing.Company == "" || c.Filing.FiscalYear == 0 || c.StartPage == 0 || c.EndPage < c.StartPage || c.Section == "" {
					t.Fatalf("incomplete metadata: %+v", c)
				}
				if c.Section == "Item 0" || c.Section == "Item 15" || c.Section == "Item 16" {
					t.Fatalf("skipped section leaked: %s", c.Section)
				}
				if hashes[c.Hash()] {
					t.Logf("duplicate chunk content at %d (%s)", i, c.Section) // boilerplate repeats are legal
				}
				hashes[c.Hash()] = true
			}
			sort.Ints(sizes)
			t.Logf("%s: %d chunks, size p10=%d p50=%d p90=%d, sections=%v", tk, len(chunks), sizes[len(sizes)/10], sizes[len(sizes)/2], sizes[len(sizes)*9/10], sections)
			if p50 := sizes[len(sizes)/2]; p50 < 900 {
				t.Errorf("median chunk too small: %d chars", p50)
			}
		})
	}
}
