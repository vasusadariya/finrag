package parse

import (
	"strings"
	"testing"
)

func extract(t *testing.T, doc string) []Block {
	t.Helper()
	b, err := ExtractBlocks(strings.NewReader(doc))
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func TestExtractBlocksPagesAndHidden(t *testing.T) {
	blocks := extract(t, `<html><body>
<ix:header><div>HIDDEN XBRL CONTEXT</div></ix:header>
<div style="display:none">also hidden</div>
<div>Page one <b>bold</b> text</div>
<div style="page-break-after:always"></div>
<div>Page&nbsp;two</div>
<div style="break-before: page">Page three</div>
<table><tr><td>Revenue</td><td>$</td><td>1,000</td></tr></table>
</body></html>`)
	var got []string
	for _, b := range blocks {
		got = append(got, b.Text+"@"+string(rune('0'+b.Page)))
	}
	want := "Page one bold text@1|Page two@2|Page three@3|Revenue $ 1,000@3"
	if strings.Join(got, "|") != want {
		t.Errorf("got  %v\nwant %s", got, want)
	}
}

func TestDropRunningText(t *testing.T) {
	var blocks []Block
	for p := 1; p <= 7; p++ {
		blocks = append(blocks,
			Block{"Table of Contents", p},
			Block{"Real paragraph on this page number that is unique " + strings.Repeat("x", p), p},
			Block{"Acme Corp | 2025 Form 10-K | " + string(rune('0'+p)), p},
			Block{string(rune('0' + p)), p},
		)
	}
	out := DropRunningText(blocks)
	if len(out) != 7 {
		t.Fatalf("kept %d blocks, want 7: %+v", len(out), out)
	}
	for _, b := range out {
		if !strings.HasPrefix(b.Text, "Real paragraph") {
			t.Errorf("furniture survived: %q", b.Text)
		}
	}
}

func TestDropRunningKeepsRepeatedTableLabelsMidPage(t *testing.T) {
	var blocks []Block
	for p := 1; p <= 8; p++ {
		for i := 0; i < 8; i++ {
			blocks = append(blocks, Block{"filler paragraph " + strings.Repeat("y", i+p), p})
		}
		blocks = append(blocks[:len(blocks)-4], append([]Block{{"Total", p}}, blocks[len(blocks)-4:]...)...)
	}
	kept := 0
	for _, b := range DropRunningText(blocks) {
		if b.Text == "Total" {
			kept++
		}
	}
	if kept != 8 {
		t.Errorf("mid-page 'Total' rows dropped: kept %d/8", kept)
	}
}

func blk(texts ...string) []Block {
	out := make([]Block, len(texts))
	for i, t := range texts {
		out[i] = Block{t, 1 + i/3}
	}
	return out
}

func pad(n int) string { return strings.Repeat("lorem ipsum ", n) }

func TestSplitSkipsTableOfContents(t *testing.T) {
	doc := Split(blk(
		"Cover", "TABLE OF CONTENTS",
		"Item 1. Business", "Item 1A. Risk Factors", "Item 7. Management's Discussion and Analysis", "Item 8. Financial Statements and Supplementary Data",
		"Item 1. Business", pad(50), "Item 1A. Risk Factors", pad(100),
		"Item 7. Management's Discussion and Analysis", pad(80), "Item 8. Financial Statements and Supplementary Data", pad(90),
	))
	for _, item := range []string{"1", "1A", "7", "8"} {
		secs := doc.Item(item)
		if len(secs) != 1 || secs[0].chars() < 500 {
			t.Fatalf("Item %s: %+v warnings=%v", item, secs, doc.Warnings)
		}
	}
	if doc.Sections[0].Item != "0" || len(doc.Sections[0].Blocks) != 6 {
		t.Errorf("cover section wrong: %+v", doc.Sections[0])
	}
	if len(doc.Warnings) != 1 || doc.Warnings[0] != "missing Item 7A" {
		t.Errorf("warnings = %v", doc.Warnings)
	}
}

func TestSplitIgnoresBareRunningHeadersAndCrossReferences(t *testing.T) {
	doc := Split(blk(
		"ITEM 1. BUSINESS", "Item 1", pad(30), "Item 1", pad(30),
		"ITEM 1A. RISK FACTORS", pad(40), "Item 7 of this report discusses our results in detail.", pad(10),
		"Item 1B, 1C", "ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS", pad(40),
	))
	var items []string
	for _, s := range doc.Sections {
		items = append(items, s.Item)
	}
	if strings.Join(items, ",") != "1,1A,7" {
		t.Fatalf("items = %v", items)
	}
	if !strings.Contains(doc.Item("1A")[0].Text(), "Item 7 of this report") {
		t.Error("cross reference should stay inside Item 1A text")
	}
}

func TestSplitHeadingAcrossTwoBlocks(t *testing.T) {
	doc := Split(blk("Item 1.", "Business", pad(40), "Item 1A.", "Risk Factors", pad(40)))
	if len(doc.Item("1")) != 1 || len(doc.Item("1A")) != 1 {
		t.Fatalf("sections: %+v", doc.Sections)
	}
	if doc.Item("1")[0].Title != "Business" {
		t.Errorf("title = %q", doc.Item("1")[0].Title)
	}
}

func TestSplitNoHeadings(t *testing.T) {
	doc := Split(blk("just text", "more text"))
	if len(doc.Sections) != 1 || doc.Sections[0].Item != "0" || len(doc.Warnings) == 0 {
		t.Errorf("%+v", doc)
	}
	if got := Split(nil); len(got.Sections) != 0 || len(got.Warnings) == 0 {
		t.Errorf("empty input: %+v", got)
	}
}

func TestSplitCarvesEmbeddedAnnualReport(t *testing.T) {
	doc := Split(blk(
		"Item 1. Business", pad(30),
		"Item 7. Management's Discussion and Analysis of Financial Condition", "See the Financial Section of this report.",
		"Item 8. Financial Statements and Supplementary Data", "See the Financial Section of this report.",
		"Item 15. Exhibits", "exhibit list", "FINANCIAL SECTION", "FINANCIAL SECTION", pad(5000),
		"REPORT OF INDEPENDENT REGISTERED PUBLIC ACCOUNTING FIRM", pad(60), pad(5000),
	))
	i7, i8 := doc.Item("7"), doc.Item("8")
	if len(i7) != 2 || len(i8) != 2 {
		t.Fatalf("i7=%d i8=%d warnings=%v", len(i7), len(i8), doc.Warnings)
	}
	if !i7[1].Synthetic || !i8[1].Synthetic || i7[1].chars() < 50000 || i8[1].chars() < 50000 {
		t.Errorf("synthetic sections wrong: %d %d", i7[1].chars(), i8[1].chars())
	}
}

func TestSplitStatementsUnderItem15(t *testing.T) {
	doc := Split(blk(
		"Item 7. Management's Discussion and Analysis", pad(2000),
		"Item 8. Financial Statements and Supplementary Data", "The information required by this Item is included in Item 15.",
		"Item 15. Exhibits and Financial Statement Schedules", "Index to financial statements",
		"Report of Independent Registered Public Accounting Firm", pad(60), pad(5000),
		"Exhibit Index", "3.1 charter",
	))
	i8 := doc.Item("8")
	if len(i8) != 2 || !i8[1].Synthetic || i8[1].chars() < 50000 {
		t.Fatalf("Item 8 not carved from Item 15: %+v", doc.Warnings)
	}
	if len(doc.Item("7")) != 1 {
		t.Error("real Item 7 must not be touched")
	}
	last := doc.Sections[len(doc.Sections)-1]
	if last.Item != "15" || !strings.Contains(last.Text(), "3.1 charter") {
		t.Errorf("exhibits should remain under Item 15: %+v", last)
	}
}

func TestExtractMeta(t *testing.T) {
	raw := []byte(`<ix:nonNumeric name="dei:DocumentFiscalYearFocus" contextRef="c1"><span>2026</span></ix:nonNumeric>
<ix:nonNumeric name="dei:EntityRegistrantName" contextRef="c1">JPMorgan Chase &amp; Co</ix:nonNumeric>`)
	m := ExtractMeta(raw)
	if m.FiscalYear != 2026 || m.Registrant != "JPMorgan Chase & Co" {
		t.Errorf("%+v", m)
	}
	if (ExtractMeta([]byte("<html></html>")) != Meta{}) {
		t.Error("missing meta should be zero")
	}
}
