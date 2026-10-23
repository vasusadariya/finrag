package parse

import (
	"html"
	"regexp"
	"strings"
)

var (
	digitsRe   = regexp.MustCompile(`\d+`)
	pageNumRe  = regexp.MustCompile(`^\(?(?:[A-Z]-)?\d{1,3}\)?\.?$`)
	maxRunning = 120
)

// runningWindow is how many blocks at the top or bottom of a page may hold repeated headers/footers.
const runningWindow = 3

// DropRunningText removes page furniture: footers/headers that repeat at the edge of many pages
// ("Table of Contents", "Apple Inc. | 2025 Form 10-K | 20") and bare page-number blocks at page edges.
func DropRunningText(blocks []Block) []Block {
	if len(blocks) == 0 {
		return blocks
	}
	first := map[int]int{} // page -> first block index
	last := map[int]int{}  // page -> last block index
	for i, b := range blocks {
		if _, ok := first[b.Page]; !ok {
			first[b.Page] = i
		}
		last[b.Page] = i
	}
	isEdge := func(i int) bool { return first[blocks[i].Page] == i || last[blocks[i].Page] == i }
	inWindow := func(i int) bool {
		return i-first[blocks[i].Page] < runningWindow || last[blocks[i].Page]-i < runningWindow
	}
	norm := func(s string) string { return strings.ToLower(digitsRe.ReplaceAllString(s, "#")) }

	windowPages := map[string]map[int]bool{}
	for i, b := range blocks {
		if len(b.Text) > maxRunning || !inWindow(i) {
			continue
		}
		n := norm(b.Text)
		if windowPages[n] == nil {
			windowPages[n] = map[int]bool{}
		}
		windowPages[n][b.Page] = true
	}
	out := make([]Block, 0, len(blocks))
	for i, b := range blocks {
		if len(b.Text) <= maxRunning {
			if isEdge(i) && pageNumRe.MatchString(b.Text) {
				continue
			}
			if inWindow(i) && len(windowPages[norm(b.Text)]) >= 5 {
				continue
			}
		}
		out = append(out, b)
	}
	return out
}

var (
	fyFocusRe  = regexp.MustCompile(`(?s)name="dei:DocumentFiscalYearFocus"[^>]*>(?:\s*<[^>]+>)*\s*(\d{4})`)
	registrant = regexp.MustCompile(`(?s)name="dei:EntityRegistrantName"[^>]*>(?:\s*<[^>]+>)*\s*([^<]+)`)
)

// Meta is cover-page metadata from the filing's iXBRL "dei" tags.
type Meta struct {
	FiscalYear int
	Registrant string
}

// ExtractMeta reads dei:* facts from raw filing HTML. Missing values are zero.
func ExtractMeta(raw []byte) Meta {
	var m Meta
	if g := fyFocusRe.FindSubmatch(raw); g != nil {
		for _, c := range g[1] {
			m.FiscalYear = m.FiscalYear*10 + int(c-'0')
		}
	}
	if g := registrant.FindSubmatch(raw); g != nil {
		m.Registrant = strings.TrimSpace(html.UnescapeString(string(g[1])))
	}
	return m
}
