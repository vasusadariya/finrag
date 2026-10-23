// Package xbrl turns SEC "companyfacts" JSON into a small, deduplicated table of annual financial facts.
package xbrl

import (
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"time"
)

// CoreConcepts are the us-gaap tags we keep. Companies report the same idea under different tags
// (e.g. revenue), so several variants are listed; the text-to-SQL glossary maps questions onto them.
var CoreConcepts = []string{
	// income statement
	"Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "RevenueFromContractWithCustomerIncludingAssessedTax",
	"SalesRevenueNet", "CostOfRevenue", "CostOfGoodsAndServicesSold", "GrossProfit",
	"ResearchAndDevelopmentExpense", "SellingGeneralAndAdministrativeExpense", "SellingAndMarketingExpense",
	"GeneralAndAdministrativeExpense", "OperatingExpenses", "OperatingIncomeLoss", "InterestExpense",
	"IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
	"IncomeTaxExpenseBenefit", "NetIncomeLoss", "EarningsPerShareBasic", "EarningsPerShareDiluted",
	"WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfSharesOutstandingBasic",
	// banks
	"InterestAndDividendIncomeOperating", "InterestIncomeExpenseNet", "NoninterestIncome", "NoninterestExpense",
	"ProvisionForLoanLeaseAndOtherLosses", "Deposits",
	// balance sheet
	"Assets", "AssetsCurrent", "Liabilities", "LiabilitiesCurrent", "StockholdersEquity",
	"CashAndCashEquivalentsAtCarryingValue", "LongTermDebt", "LongTermDebtNoncurrent",
	// cash flow
	"NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInInvestingActivities",
	"NetCashProvidedByUsedInFinancingActivities", "PaymentsToAcquirePropertyPlantAndEquipment",
	"PaymentsForRepurchaseOfCommonStock", "PaymentsOfDividends", "PaymentsOfDividendsCommonStock",
	"DepreciationDepletionAndAmortization", "ShareBasedCompensation",
}

// Fact is one annual value (flow over ~1 year) or year-end balance.
type Fact struct {
	Concept      string
	Unit         string
	PeriodStart  *time.Time // nil for balance-sheet (instant) facts
	PeriodEnd    time.Time
	Value        string // decimal as printed in the JSON, never rounded through float
	FiscalYear   int    // fiscal year of the PERIOD (not of the filing that reported it)
	FiscalPeriod string // always "FY"
	Accession    string // latest 10-K / 10-K/A that reported this value
	Filed        time.Time
}

type entry struct {
	Start string      `json:"start"`
	End   string      `json:"end"`
	Val   json.Number `json:"val"`
	Accn  string      `json:"accn"`
	FY    int         `json:"fy"`
	FP    string      `json:"fp"`
	Form  string      `json:"form"`
	Filed string      `json:"filed"`
}

type companyFacts struct {
	CIK   json.Number `json:"cik"`
	Facts map[string]map[string]struct {
		Units map[string][]entry `json:"units"`
	} `json:"facts"`
}

type anchor struct {
	end time.Time
	fy  int
}

const day = 24 * time.Hour

// keepYears limits output to recent fiscal years: the SEC's own fy labels on very old filings are unreliable,
// and the product only needs a few years of history.
const keepYears = 5

func date(s string) (time.Time, error) { return time.Parse("2006-01-02", s) }

func isAnnualForm(f string) bool { return f == "10-K" || f == "10-K/A" }

// Parse extracts core annual facts from a companyfacts JSON body.
func Parse(body []byte) ([]Fact, error) {
	var cf companyFacts
	if err := json.Unmarshal(body, &cf); err != nil {
		return nil, fmt.Errorf("xbrl: parse companyfacts: %w", err)
	}
	gaap := cf.Facts["us-gaap"]
	core := map[string]bool{}
	for _, c := range CoreConcepts {
		core[c] = true
	}

	// 1. Fiscal-year anchors: in each 10-K the latest period end is the fiscal year end, labelled with that filing's fy.
	latest := map[string]anchor{} // accession -> anchor
	for concept, c := range gaap {
		if !core[concept] {
			continue
		}
		for _, es := range c.Units {
			for _, e := range es {
				if !isAnnualForm(e.Form) || e.FP != "FY" {
					continue
				}
				end, err := date(e.End)
				if err != nil {
					return nil, fmt.Errorf("xbrl: bad end date %q in %s", e.End, concept)
				}
				if a, ok := latest[e.Accn]; !ok || end.After(a.end) {
					latest[e.Accn] = anchor{end, e.FY}
				}
			}
		}
	}
	if len(latest) == 0 {
		return nil, fmt.Errorf("xbrl: no 10-K facts found")
	}
	anchors := make([]anchor, 0, len(latest))
	for _, a := range latest {
		anchors = append(anchors, a)
	}
	sort.Slice(anchors, func(i, j int) bool { return anchors[i].end.Before(anchors[j].end) })
	nearest := func(t time.Time) (anchor, time.Duration) {
		best, bestD := anchors[0], time.Duration(math.MaxInt64)
		for _, a := range anchors {
			d := t.Sub(a.end)
			if d < 0 {
				d = -d
			}
			if d < bestD {
				best, bestD = a, d
			}
		}
		return best, bestD
	}
	fiscalYear := func(end time.Time) int {
		a, _ := nearest(end)
		return a.fy + int(math.Round(end.Sub(a.end).Hours()/24/365.25))
	}

	cutoff := anchors[len(anchors)-1].end.AddDate(-keepYears, 0, 0).Add(-30 * day)

	// 2. Keep annual flows (~1 year) and year-end balances; dedupe to the latest filing per (concept, unit, period).
	type key struct {
		concept, unit, start, end string
	}
	best := map[key]Fact{}
	for concept, c := range gaap {
		if !core[concept] {
			continue
		}
		for unit, es := range c.Units {
			for _, e := range es {
				if !isAnnualForm(e.Form) {
					continue
				}
				end, err := date(e.End)
				if err != nil {
					return nil, err
				}
				if end.Before(cutoff) {
					continue
				}
				var start *time.Time
				if e.Start != "" {
					s, err := date(e.Start)
					if err != nil {
						return nil, err
					}
					if days := end.Sub(s).Hours() / 24; days < 350 || days > 380 {
						continue // quarterly or other partial-year duration
					}
					start = &s
				} else if _, d := nearest(end); d > 7*day {
					continue // instant that is not at a fiscal year end
				}
				filed, err := date(e.Filed)
				if err != nil {
					return nil, fmt.Errorf("xbrl: bad filed date %q", e.Filed)
				}
				k := key{concept, unit, e.Start, e.End}
				if old, ok := best[k]; ok && !filed.After(old.Filed) {
					continue
				}
				best[k] = Fact{Concept: concept, Unit: unit, PeriodStart: start, PeriodEnd: end, Value: e.Val.String(),
					FiscalYear: fiscalYear(end), FiscalPeriod: "FY", Accession: e.Accn, Filed: filed}
			}
		}
	}
	out := make([]Fact, 0, len(best))
	for _, f := range best {
		out = append(out, f)
	}
	sort.Slice(out, func(i, j int) bool {
		a, b := out[i], out[j]
		if a.Concept != b.Concept {
			return a.Concept < b.Concept
		}
		if a.Unit != b.Unit {
			return a.Unit < b.Unit
		}
		if !a.PeriodEnd.Equal(b.PeriodEnd) {
			return a.PeriodEnd.Before(b.PeriodEnd)
		}
		return a.PeriodStart != nil && (b.PeriodStart == nil || a.PeriodStart.Before(*b.PeriodStart))
	})
	return out, nil
}
