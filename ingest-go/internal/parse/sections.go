package parse

import (
	"fmt"
	"regexp"
	"strings"
)

// Section is a contiguous run of blocks belonging to one 10-K Item.
type Section struct {
	Item      string // "1", "1A", "7", ... or "0" for the cover/table of contents
	Title     string
	Blocks    []Block
	Synthetic bool // true when carved out of a trailing "annual report" section (e.g. JPM, XOM)
}

func (s Section) StartPage() int { return s.Blocks[0].Page }
func (s Section) EndPage() int   { return s.Blocks[len(s.Blocks)-1].Page }

func (s Section) Text() string {
	parts := make([]string, len(s.Blocks))
	for i, b := range s.Blocks {
		parts[i] = b.Text
	}
	return strings.Join(parts, "\n")
}

func (s Section) chars() int {
	n := 0
	for _, b := range s.Blocks {
		n += len(b.Text)
	}
	return n
}

// Document is a filing split into sections, plus anything unusual noticed on the way.
type Document struct {
	Sections []Section
	Warnings []string
}

// Section returns all sections for an Item (more than one when a synthetic split was needed).
func (d Document) Item(item string) []Section {
	var out []Section
	for _, s := range d.Sections {
		if s.Item == item {
			out = append(out, s)
		}
	}
	return out
}

var itemOrder = []string{"1", "1A", "1B", "1C", "2", "3", "4", "5", "6", "7", "7A", "8", "9", "9A", "9B", "9C",
	"10", "11", "12", "13", "14", "15", "16"}

// titleRe holds the expected heading text for each Item. Requiring it keeps cross-references
// ("Item 7 of this report...") and running page headers from being mistaken for headings.
var titleRe = map[string]*regexp.Regexp{
	"1":  regexp.MustCompile(`(?i)^business`),
	"1A": regexp.MustCompile(`(?i)^risk factors`),
	"1B": regexp.MustCompile(`(?i)^unresolved staff comments`),
	"1C": regexp.MustCompile(`(?i)^cybersecurity`),
	"2":  regexp.MustCompile(`(?i)^properties`),
	"3":  regexp.MustCompile(`(?i)^legal proceedings`),
	"4":  regexp.MustCompile(`(?i)^mine safety`),
	"5":  regexp.MustCompile(`(?i)^market for`),
	"6":  regexp.MustCompile(`(?i)^(\[?reserved\]?|selected (consolidated )?financial data)`),
	"7":  regexp.MustCompile(`(?i)^management.?s discussion`),
	"7A": regexp.MustCompile(`(?i)^quantitative and qualitative`),
	"8":  regexp.MustCompile(`(?i)^financial statements and supplementary`),
	"9":  regexp.MustCompile(`(?i)^changes in and disagreements`),
	"9A": regexp.MustCompile(`(?i)^controls and procedures`),
	"9B": regexp.MustCompile(`(?i)^other information`),
	"9C": regexp.MustCompile(`(?i)^disclosure regarding foreign`),
	"10": regexp.MustCompile(`(?i)^directors, executive officers`),
	"11": regexp.MustCompile(`(?i)^executive compensation`),
	"12": regexp.MustCompile(`(?i)^security ownership`),
	"13": regexp.MustCompile(`(?i)^certain relationships`),
	"14": regexp.MustCompile(`(?i)^principal account`),
	"15": regexp.MustCompile(`(?i)^exhibits?`),
	"16": regexp.MustCompile(`(?i)^form 10-k summary`),
}

var headingRe = regexp.MustCompile(`(?i)^item\s*(\d{1,2}[abc]?)\s*[.:\-–—]?\s*(.*)$`)

type candidate struct {
	idx   int // block index of the heading
	key   string
	title string
}

func findCandidates(blocks []Block) []candidate {
	var out []candidate
	for i, b := range blocks {
		if len(b.Text) > 250 {
			continue
		}
		m := headingRe.FindStringSubmatch(b.Text)
		if m == nil {
			continue
		}
		key := strings.ToUpper(m[1])
		re, ok := titleRe[key]
		if !ok {
			continue
		}
		title := strings.TrimSpace(m[2])
		if title == "" && i+1 < len(blocks) && len(blocks[i+1].Text) <= 250 {
			title = blocks[i+1].Text // "Item 5." and "Market for ..." in separate blocks
		}
		if re.MatchString(title) {
			out = append(out, candidate{idx: i, key: key, title: title})
		}
	}
	return out
}

// Split divides a filing into Item sections.
func Split(blocks []Block) Document {
	var doc Document
	if len(blocks) == 0 {
		doc.Warnings = append(doc.Warnings, "no text extracted")
		return doc
	}
	cands := findCandidates(blocks)
	// cumulative character offsets for span comparison
	off := make([]int, len(blocks)+1)
	for i, b := range blocks {
		off[i+1] = off[i] + len(b.Text)
	}
	span := func(ci int) int {
		end := len(blocks)
		if ci+1 < len(cands) {
			end = cands[ci+1].idx
		}
		return off[end] - off[cands[ci].idx]
	}
	// Best chain of headings in strictly increasing Item order, maximising the text they cover. A table of
	// contents (many tiny spans) or a stray cross-reference loses to the real headings.
	dp := make([]int, len(cands))
	prev := make([]int, len(cands))
	bestEnd := -1
	for ci := range cands {
		dp[ci], prev[ci] = span(ci), -1
		for pj := 0; pj < ci; pj++ {
			if rank(cands[pj].key) < rank(cands[ci].key) && dp[pj]+span(ci) > dp[ci] {
				dp[ci], prev[ci] = dp[pj]+span(ci), pj
			}
		}
		if bestEnd < 0 || dp[ci] > dp[bestEnd] {
			bestEnd = ci
		}
	}
	var chosen []candidate
	for ci := bestEnd; ci >= 0; ci = prev[ci] {
		chosen = append([]candidate{cands[ci]}, chosen...)
	}

	if len(chosen) == 0 {
		doc.Warnings = append(doc.Warnings, "no Item headings found")
		doc.Sections = []Section{{Item: "0", Title: "Unsectioned", Blocks: blocks}}
		return doc
	}
	if chosen[0].idx > 0 {
		doc.Sections = append(doc.Sections, Section{Item: "0", Title: "Cover and table of contents", Blocks: blocks[:chosen[0].idx]})
	}
	for i, c := range chosen {
		end := len(blocks)
		if i+1 < len(chosen) {
			end = chosen[i+1].idx
		}
		doc.Sections = append(doc.Sections, Section{Item: c.key, Title: c.title, Blocks: blocks[c.idx:end]})
	}
	for _, k := range []string{"1", "1A", "7", "7A", "8"} {
		if len(doc.Item(k)) == 0 {
			doc.Warnings = append(doc.Warnings, "missing Item "+k)
		}
	}
	splitTrailingFinancialSection(&doc)
	return doc
}

func rank(key string) int {
	for i, k := range itemOrder {
		if k == key {
			return i
		}
	}
	return -1
}

var (
	finStartRe = regexp.MustCompile(`(?i)^(financial section|financial review|management.?s discussion and analysis( of financial condition and results of operations)?):?$`)
	auditRe    = regexp.MustCompile(`(?i)^report of independent registered public accounting firm`)
)

const (
	stubMaxChars     = 2000  // Item 7/8 shorter than this are treated as cross-reference stubs
	trailingMinChars = 50000 // a trailing Item 15/16 section this large holds an embedded annual report
)

var exhibitIndexRe = regexp.MustCompile(`(?i)^(exhibit index|index to exhibits|\(a\)\(3\) exhibits|exhibits)$`)

// splitTrailingFinancialSection handles filers whose Item 7 and/or Item 8 only point elsewhere:
//   - JPM, XOM: both point at an "annual report" section appended after the last Item;
//   - NVDA: Item 8 points at the financial statements placed under Item 15.
//
// The target region is carved into synthetic Item 7 (MD&A) and Item 8 (statements and notes) sections so
// retrieval filters on them still work.
func splitTrailingFinancialSection(doc *Document) {
	i7, i8 := doc.Item("7"), doc.Item("8")
	if len(i7) != 1 || len(i8) != 1 {
		return
	}
	stub7, stub8 := i7[0].chars() <= stubMaxChars, i8[0].chars() <= stubMaxChars
	if !stub8 {
		return
	}
	// the largest trailing section (Item 15 or 16) is where the content lives
	tailIdx := -1
	for i, s := range doc.Sections {
		if (s.Item == "15" || s.Item == "16") && s.chars() >= trailingMinChars && (tailIdx < 0 || s.chars() > doc.Sections[tailIdx].chars()) {
			tailIdx = i
		}
	}
	if tailIdx < 0 {
		return
	}
	tail := doc.Sections[tailIdx]

	start := 1 // Item 8 only: statements start at the beginning of the section (after the heading)
	if stub7 {
		start = -1
		for i := 1; i < len(tail.Blocks); i++ {
			if finStartRe.MatchString(tail.Blocks[i].Text) {
				start = i
				break
			}
		}
		if start < 0 {
			doc.Warnings = append(doc.Warnings, "Item 7/8 are cross-references but no embedded financial section start was found")
			return
		}
	}
	split := -1
	for i := start; i < len(tail.Blocks); i++ {
		if !auditRe.MatchString(tail.Blocks[i].Text) {
			continue
		}
		n := 0
		for j := i + 1; j < len(tail.Blocks) && j <= i+3; j++ {
			n += len(tail.Blocks[j].Text)
		}
		if n > 300 { // prose, not a table-of-contents line
			split = i
			break
		}
	}
	if split < 0 {
		doc.Warnings = append(doc.Warnings, "embedded financial section found but audit report (start of Item 8) was not")
		return
	}
	end := len(tail.Blocks)
	for i := split; i < len(tail.Blocks); i++ {
		if exhibitIndexRe.MatchString(tail.Blocks[i].Text) {
			end = i
			break
		}
	}
	var added []Section
	head := tail.Blocks[:start]
	if stub7 {
		added = append(added, Section{Item: "7", Title: "Management's Discussion and Analysis (annual report section)", Blocks: tail.Blocks[start:split], Synthetic: true})
	} else {
		head = tail.Blocks[:split]
	}
	added = append(added, Section{Item: "8", Title: "Financial Statements and Notes (embedded section)", Blocks: tail.Blocks[split:end], Synthetic: true})
	doc.Sections[tailIdx] = Section{Item: tail.Item, Title: tail.Title, Blocks: head}
	if end < len(tail.Blocks) {
		added = append(added, Section{Item: tail.Item, Title: tail.Title + " (exhibits)", Blocks: tail.Blocks[end:]})
	}
	rest := append([]Section{}, doc.Sections[tailIdx+1:]...)
	doc.Sections = append(append(doc.Sections[:tailIdx+1], added...), rest...)
	doc.Warnings = append(doc.Warnings, "Item 7/8 content carved from the trailing section of Item "+tail.Item)
}

// MinItemChars are the smallest plausible sizes (summed over all sections of the Item) for a real 10-K of the
// kind we ingest. Below these a parse has almost certainly lost content (see the JPM annual-report case).
var MinItemChars = map[string]int{"1": 5000, "1A": 20000, "7": 10000, "8": 30000}

// CheckPlausible reports parses that look like silent data loss, so callers can refuse to load them.
func (d Document) CheckPlausible() error {
	var problems []string
	for _, item := range []string{"1", "1A", "7", "8"} {
		total := 0
		for _, s := range d.Item(item) {
			total += s.chars()
		}
		if min := MinItemChars[item]; total < min {
			problems = append(problems, fmt.Sprintf("Item %s has %d chars (< %d)", item, total, min))
		}
	}
	if len(problems) == 0 {
		return nil
	}
	return fmt.Errorf("parse looks incomplete: %s; warnings: %v", strings.Join(problems, "; "), d.Warnings)
}
