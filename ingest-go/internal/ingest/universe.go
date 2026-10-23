// Package ingest orchestrates download -> parse -> chunk -> embed -> load for a set of companies.
package ingest

import "strings"

type Company struct {
	Ticker string
	CIK    string
	Name   string
}

// Universe is the fixed set of companies the product covers.
var Universe = []Company{
	{"AAPL", "320193", "Apple Inc."},
	{"MSFT", "789019", "Microsoft Corporation"},
	{"GOOGL", "1652044", "Alphabet Inc."},
	{"AMZN", "1018724", "Amazon.com, Inc."},
	{"NVDA", "1045810", "NVIDIA Corporation"},
	{"META", "1326801", "Meta Platforms, Inc."},
	{"JPM", "19617", "JPMorgan Chase & Co."},
	{"XOM", "34088", "Exxon Mobil Corporation"},
}

// Select returns the universe companies matching a comma-separated ticker list (empty = all).
func Select(tickers string) ([]Company, []string) {
	if strings.TrimSpace(tickers) == "" {
		return Universe, nil
	}
	var out []Company
	var unknown []string
	for _, t := range strings.Split(tickers, ",") {
		t = strings.ToUpper(strings.TrimSpace(t))
		found := false
		for _, c := range Universe {
			if c.Ticker == t {
				out, found = append(out, c), true
			}
		}
		if !found && t != "" {
			unknown = append(unknown, t)
		}
	}
	return out, unknown
}
