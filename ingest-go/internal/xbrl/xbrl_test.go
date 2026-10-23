package xbrl

import (
	"bytes"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/vasusadariya/finrag/ingest-go/internal/parse"
)

// A tiny two-year company: FY ends 2025-09-27 and 2024-09-28; FY2025's 10-K also reports FY2024 comparatives,
// FY2024 was restated in the later filing, plus a 10-Q, a quarterly value and a mid-year balance that must be dropped.
const fixture = `{"cik":1,"entityName":"Acme","facts":{"us-gaap":{
"NetIncomeLoss":{"units":{"USD":[
 {"start":"2023-10-01","end":"2024-09-28","val":100,"accn":"A-24","fy":2024,"fp":"FY","form":"10-K","filed":"2024-11-01"},
 {"start":"2023-10-01","end":"2024-09-28","val":105,"accn":"A-25","fy":2025,"fp":"FY","form":"10-K","filed":"2025-11-01"},
 {"start":"2024-09-29","end":"2025-09-27","val":120,"accn":"A-25","fy":2025,"fp":"FY","form":"10-K","filed":"2025-11-01"},
 {"start":"2025-03-30","end":"2025-06-28","val":30,"accn":"Q-25","fy":2025,"fp":"Q3","form":"10-Q","filed":"2025-08-01"},
 {"start":"2024-12-29","end":"2025-03-29","val":28,"accn":"A-25","fy":2025,"fp":"FY","form":"10-K","filed":"2025-11-01"}
]}},
"Assets":{"units":{"USD":[
 {"end":"2024-09-28","val":900,"accn":"A-24","fy":2024,"fp":"FY","form":"10-K","filed":"2024-11-01"},
 {"end":"2025-09-27","val":1000,"accn":"A-25","fy":2025,"fp":"FY","form":"10-K","filed":"2025-11-01"},
 {"end":"2025-06-28","val":950,"accn":"Q-25","fy":2025,"fp":"Q3","form":"10-Q","filed":"2025-08-01"},
 {"end":"2025-03-29","val":940,"accn":"A-25","fy":2025,"fp":"FY","form":"10-K","filed":"2025-11-01"}
]}},
"EarningsPerShareDiluted":{"units":{"USD/shares":[
 {"start":"2024-09-29","end":"2025-09-27","val":6.11,"accn":"A-25","fy":2025,"fp":"FY","form":"10-K","filed":"2025-11-01"}
]}},
"SomeUnrelatedConcept":{"units":{"USD":[
 {"start":"2024-09-29","end":"2025-09-27","val":1,"accn":"A-25","fy":2025,"fp":"FY","form":"10-K","filed":"2025-11-01"}
]}}
}}}`

func find(fs []Fact, concept string, fy int) *Fact {
	for i := range fs {
		if fs[i].Concept == concept && fs[i].FiscalYear == fy {
			return &fs[i]
		}
	}
	return nil
}

func TestParseFixture(t *testing.T) {
	fs, err := Parse([]byte(fixture))
	if err != nil {
		t.Fatal(err)
	}
	if len(fs) != 5 {
		t.Fatalf("got %d facts, want 5: %+v", len(fs), fs)
	}
	ni25, ni24 := find(fs, "NetIncomeLoss", 2025), find(fs, "NetIncomeLoss", 2024)
	if ni25 == nil || ni25.Value != "120" || ni25.Accession != "A-25" {
		t.Errorf("FY2025 net income: %+v", ni25)
	}
	// comparative reported in the FY2025 10-K belongs to fiscal year 2024 (not 2025) and uses the restated value
	if ni24 == nil || ni24.Value != "105" || ni24.Accession != "A-25" || ni24.FiscalYear != 2024 {
		t.Errorf("FY2024 net income: %+v", ni24)
	}
	if a := find(fs, "Assets", 2025); a == nil || a.PeriodStart != nil || a.Value != "1000" {
		t.Errorf("assets: %+v", a)
	}
	if eps := find(fs, "EarningsPerShareDiluted", 2025); eps == nil || eps.Value != "6.11" || eps.Unit != "USD/shares" {
		t.Errorf("eps keeps decimals exactly: %+v", eps)
	}
	for _, f := range fs {
		if f.Concept == "SomeUnrelatedConcept" {
			t.Error("non-core concept leaked")
		}
		if f.FiscalPeriod != "FY" {
			t.Errorf("fiscal period %q", f.FiscalPeriod)
		}
	}
}

func TestParseErrors(t *testing.T) {
	if _, err := Parse([]byte("nope")); err == nil {
		t.Error("expected json error")
	}
	if _, err := Parse([]byte(`{"facts":{"us-gaap":{}}}`)); err == nil {
		t.Error("expected error when no 10-K facts")
	}
}

func filingsDir() string {
	if d := os.Getenv("FINRAG_FILINGS_DIR"); d != "" {
		return d
	}
	return filepath.Join("..", "..", "testdata", "filings")
}

func loadFacts(t *testing.T, ticker string) []Fact {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(filingsDir(), "..", "facts", ticker+".json"))
	if err != nil {
		t.Skipf("companyfacts for %s not present (run scripts/fetch-sample-filings.sh)", ticker)
	}
	fs, err := Parse(b)
	if err != nil {
		t.Fatal(err)
	}
	return fs
}

var tickers = []string{"AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "JPM", "XOM"}

// Known values read off the SEC filings / EDGAR frontend.
func TestKnownValues(t *testing.T) {
	cases := []struct {
		ticker, concept string
		fy              int
		value           string
	}{
		{"AAPL", "RevenueFromContractWithCustomerExcludingAssessedTax", 2025, "416161000000"},
		{"AAPL", "RevenueFromContractWithCustomerExcludingAssessedTax", 2024, "391035000000"},
		{"AAPL", "RevenueFromContractWithCustomerExcludingAssessedTax", 2023, "383285000000"},
		{"NVDA", "Assets", 2026, "206803000000"},
	}
	for _, c := range cases {
		f := find(loadFacts(t, c.ticker), c.concept, c.fy)
		if f == nil || f.Value != c.value {
			t.Errorf("%s %s FY%d: got %+v, want %s", c.ticker, c.concept, c.fy, f, c.value)
		}
	}
}

func TestRealDataInvariants(t *testing.T) {
	for _, tk := range tickers {
		t.Run(tk, func(t *testing.T) {
			fs := loadFacts(t, tk)
			if len(fs) < 100 {
				t.Errorf("only %d facts", len(fs))
			}
			seen := map[string]bool{}
			maxFY, haveRevenue := 0, false
			for _, f := range fs {
				if f.PeriodStart != nil {
					days := f.PeriodEnd.Sub(*f.PeriodStart).Hours() / 24
					if days < 350 || days > 380 {
						t.Fatalf("duration %v days for %s", days, f.Concept)
					}
					// one annual value per concept, unit and fiscal year
					k := fmt.Sprintf("%s|%s|%d", f.Concept, f.Unit, f.FiscalYear)
					if seen[k] {
						t.Errorf("duplicate annual fact %s", k)
					}
					seen[k] = true
				}
				if f.Value == "" || f.Accession == "" || f.Unit == "" || f.FiscalYear < 2000 {
					t.Fatalf("incomplete fact %+v", f)
				}
				if f.FiscalYear > maxFY {
					maxFY = f.FiscalYear
				}
				if strings.Contains(f.Concept, "Revenue") || f.Concept == "Revenues" {
					haveRevenue = true
				}
			}
			if !haveRevenue {
				t.Error("no revenue concept at all")
			}
			if find(fs, "NetIncomeLoss", maxFY) == nil || find(fs, "Assets", maxFY) == nil {
				t.Errorf("latest FY%d lacks NetIncomeLoss or Assets", maxFY)
			}
			// at least 3 consecutive fiscal years of net income
			for _, y := range []int{maxFY, maxFY - 1, maxFY - 2} {
				if find(fs, "NetIncomeLoss", y) == nil {
					t.Errorf("NetIncomeLoss missing for FY%d", y)
				}
			}
		})
	}
}

func commas(n int64) string {
	s := fmt.Sprintf("%d", n)
	var out []byte
	for i, c := range []byte(s) {
		if i > 0 && (len(s)-i)%3 == 0 {
			out = append(out, ',')
		}
		out = append(out, c)
	}
	return string(out)
}

// XBRL (data.sec.gov) and the human-readable 10-K (www.sec.gov) are independent sources; the latest-year
// net income and total assets from XBRL must literally appear, in millions, in the filing's text.
func TestXBRLMatchesFilingText(t *testing.T) {
	for _, tk := range tickers {
		t.Run(tk, func(t *testing.T) {
			fs := loadFacts(t, tk)
			raw, err := os.ReadFile(filepath.Join(filingsDir(), tk+".htm"))
			if err != nil {
				t.Skip("filing html not present")
			}
			blocks, err := parse.ExtractBlocks(bytes.NewReader(raw))
			if err != nil {
				t.Fatal(err)
			}
			var sb strings.Builder
			for _, b := range blocks {
				sb.WriteString(b.Text)
				sb.WriteByte('\n')
			}
			text := sb.String()
			maxFY := 0
			for _, f := range fs {
				maxFY = max(maxFY, f.FiscalYear)
			}
			for _, concept := range []string{"NetIncomeLoss", "Assets"} {
				f := find(fs, concept, maxFY)
				if f == nil {
					t.Fatalf("no %s for FY%d", concept, maxFY)
				}
				var v int64
				if _, err := fmt.Sscanf(f.Value, "%d", &v); err != nil || v%1_000_000 != 0 {
					t.Skipf("%s not whole millions: %s", concept, f.Value)
				}
				if want := commas(v / 1_000_000); !strings.Contains(text, want) {
					t.Errorf("%s FY%d = %s (%s million) not found in the 10-K text", concept, maxFY, f.Value, want)
				}
			}
		})
	}
}
