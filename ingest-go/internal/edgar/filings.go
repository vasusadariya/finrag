package edgar

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"
)

// Endpoints holds EDGAR base URLs; overridable for tests.
type Endpoints struct {
	Data     string // submissions + XBRL, e.g. https://data.sec.gov
	Archives string // filing documents, e.g. https://www.sec.gov/Archives/edgar/data
}

var DefaultEndpoints = Endpoints{
	Data:     "https://data.sec.gov",
	Archives: "https://www.sec.gov/Archives/edgar/data",
}

// Filing is one 10-K entry from a company's submissions index.
type Filing struct {
	CIK        string
	Accession  string // e.g. 0000320193-24-000123
	Form       string
	FiledAt    time.Time
	PeriodEnd  time.Time
	PrimaryDoc string
	FiscalYear int // calendar year of PeriodEnd's fiscal year end
}

// PadCIK returns the 10-digit zero-padded CIK.
func PadCIK(cik string) string {
	cik = strings.TrimLeft(cik, "0")
	return fmt.Sprintf("%010s", cik)
}

func (e Endpoints) SubmissionsURL(cik string) string {
	return fmt.Sprintf("%s/submissions/CIK%s.json", e.Data, PadCIK(cik))
}

func (e Endpoints) CompanyFactsURL(cik string) string {
	return fmt.Sprintf("%s/api/xbrl/companyfacts/CIK%s.json", e.Data, PadCIK(cik))
}

func (e Endpoints) DocumentURL(f Filing) string {
	acc := strings.ReplaceAll(f.Accession, "-", "")
	return fmt.Sprintf("%s/%s/%s/%s", e.Archives, strings.TrimLeft(f.CIK, "0"), acc, f.PrimaryDoc)
}

// filingArrays is the column-oriented filing list used both in a submissions "recent" block and,
// at top level, in the older paged files.
type filingArrays struct {
	Accession  []string `json:"accessionNumber"`
	FilingDate []string `json:"filingDate"`
	ReportDate []string `json:"reportDate"`
	Form       []string `json:"form"`
	PrimaryDoc []string `json:"primaryDocument"`
}

type submissions struct {
	CIK     string `json:"cik"`
	Filings struct {
		Recent filingArrays `json:"recent"`
		Files  []struct {
			Name     string `json:"name"`
			FilingTo string `json:"filingTo"`
		} `json:"files"`
	} `json:"filings"`
}

func tenKsFrom(cik string, r filingArrays) ([]Filing, error) {
	n := len(r.Accession)
	if len(r.FilingDate) != n || len(r.ReportDate) != n || len(r.Form) != n || len(r.PrimaryDoc) != n {
		return nil, fmt.Errorf("edgar: submissions arrays have inconsistent lengths")
	}
	var out []Filing
	for i := 0; i < n; i++ {
		if r.Form[i] != "10-K" {
			continue
		}
		filed, err1 := time.Parse("2006-01-02", r.FilingDate[i])
		period, err2 := time.Parse("2006-01-02", r.ReportDate[i])
		if err1 != nil || err2 != nil {
			return nil, fmt.Errorf("edgar: bad date in filing %s", r.Accession[i])
		}
		out = append(out, Filing{
			CIK:        strings.TrimLeft(cik, "0"),
			Accession:  r.Accession[i],
			Form:       r.Form[i],
			FiledAt:    filed,
			PeriodEnd:  period,
			PrimaryDoc: r.PrimaryDoc[i],
			FiscalYear: fiscalYear(period),
		})
	}
	return out, nil
}

func sortNewestFirst(fs []Filing) {
	sort.Slice(fs, func(i, j int) bool { return fs[i].PeriodEnd.After(fs[j].PeriodEnd) })
}

// ParseTenKs extracts 10-K filings (not amendments) from the "recent" block of a submissions JSON body,
// newest first. Companies with heavy filing activity have older 10-Ks in paged files; see LatestTenKs.
func ParseTenKs(body []byte) ([]Filing, error) {
	var s submissions
	if err := json.Unmarshal(body, &s); err != nil {
		return nil, fmt.Errorf("edgar: parse submissions: %w", err)
	}
	out, err := tenKsFrom(s.CIK, s.Filings.Recent)
	if err != nil {
		return nil, err
	}
	sortNewestFirst(out)
	return out, nil
}

// fiscalYear follows the common convention: a year ending in January–early February belongs to the prior year
// (retailers); everything else is the calendar year of the period end.
func fiscalYear(period time.Time) int {
	if period.Month() == time.January {
		return period.Year() - 1
	}
	return period.Year()
}

// LatestTenKs returns up to n most recent 10-Ks for a company. The submissions "recent" block only covers
// roughly the last thousand filings, so for companies that file a lot (insider forms, 8-Ks) the older paged
// files are fetched, newest first, until n 10-Ks are found.
func (c *Client) LatestTenKs(ctx context.Context, ep Endpoints, cik string, n int) ([]Filing, error) {
	body, err := c.Get(ctx, ep.SubmissionsURL(cik))
	if err != nil {
		return nil, err
	}
	var s submissions
	if err := json.Unmarshal(body, &s); err != nil {
		return nil, fmt.Errorf("edgar: parse submissions: %w", err)
	}
	all, err := tenKsFrom(s.CIK, s.Filings.Recent)
	if err != nil {
		return nil, err
	}
	files := s.Filings.Files
	sort.Slice(files, func(i, j int) bool { return files[i].FilingTo > files[j].FilingTo })
	for _, f := range files {
		if len(all) >= n {
			break
		}
		page, err := c.Get(ctx, ep.Data+"/submissions/"+f.Name)
		if err != nil {
			return nil, fmt.Errorf("edgar: older submissions page %s: %w", f.Name, err)
		}
		var arrays filingArrays
		if err := json.Unmarshal(page, &arrays); err != nil {
			return nil, fmt.Errorf("edgar: parse %s: %w", f.Name, err)
		}
		older, err := tenKsFrom(s.CIK, arrays)
		if err != nil {
			return nil, err
		}
		all = append(all, older...)
	}
	sortNewestFirst(all)
	if len(all) > n {
		all = all[:n]
	}
	return all, nil
}

// FetchDocument downloads a filing's primary document HTML.
func (c *Client) FetchDocument(ctx context.Context, ep Endpoints, f Filing) ([]byte, error) {
	return c.Get(ctx, ep.DocumentURL(f))
}

// FetchCompanyFacts downloads the XBRL companyfacts JSON.
func (c *Client) FetchCompanyFacts(ctx context.Context, ep Endpoints, cik string) ([]byte, error) {
	return c.Get(ctx, ep.CompanyFactsURL(cik))
}
