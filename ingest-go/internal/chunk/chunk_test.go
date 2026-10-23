package chunk

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"testing"

	"github.com/vasusadariya/finrag/ingest-go/internal/parse"
)

var numRe = regexp.MustCompile(`Sentence number (\d+) `)

var filing = Filing{Ticker: "ACME", Company: "Acme Corp", FiscalYear: 2025, Form: "10-K", Accession: "0000000000-26-000001"}

func sentence(i int) string {
	return fmt.Sprintf("Sentence number %d describes a material fact about the business in some detail. ", i)
}

func para(start, n int) string {
	var sb strings.Builder
	for i := 0; i < n; i++ {
		sb.WriteString(sentence(start + i))
	}
	return strings.TrimSpace(sb.String())
}

func mkSection(item string, page int, paras ...string) parse.Section {
	var blocks []parse.Block
	for i, p := range paras {
		blocks = append(blocks, parse.Block{Text: p, Page: page + i/4})
	}
	return parse.Section{Item: item, Title: "Title " + item, Blocks: blocks}
}

func TestSizeBoundsAndNoCrossingSections(t *testing.T) {
	var a, b []string
	for i := 0; i < 60; i++ {
		a = append(a, para(i*5, 5))
		b = append(b, para(1000+i*5, 5))
	}
	doc := parse.Document{Sections: []parse.Section{mkSection("1", 1, a...), mkSection("1A", 20, b...)}}
	chunks := Document(filing, doc, Options{})
	if len(chunks) < 10 {
		t.Fatalf("only %d chunks", len(chunks))
	}
	for i, c := range chunks {
		if len(c.Text) > MaxChars {
			t.Errorf("chunk %d has %d chars > %d", i, len(c.Text), MaxChars)
		}
		if i < len(chunks)-1 && chunks[i+1].Section == c.Section && len(c.Text) < MaxChars/2 {
			t.Errorf("non-final chunk %d too small: %d chars", i, len(c.Text))
		}
		// Item 1 sentences are numbered < 1000, Item 1A sentences >= 1000
		for _, m := range numRe.FindAllStringSubmatch(c.Text, -1) {
			n, _ := strconv.Atoi(m[1])
			if (c.Section == "Item 1") != (n < 1000) {
				t.Fatalf("chunk %d (%s) contains sentence %d from the other section", i, c.Section, n)
			}
		}
		if c.Index != i {
			t.Errorf("index %d != %d", c.Index, i)
		}
	}
}

func TestOverlapBetweenConsecutiveChunks(t *testing.T) {
	var paras []string
	for i := 0; i < 80; i++ {
		paras = append(paras, para(i*3, 3))
	}
	chunks := Document(filing, parse.Document{Sections: []parse.Section{mkSection("7", 1, paras...)}}, Options{})
	if len(chunks) < 3 {
		t.Fatal("need several chunks")
	}
	for i := 0; i+1 < len(chunks); i++ {
		if overlapLen(chunks[i].Text, chunks[i+1].Text) == 0 {
			t.Errorf("chunks %d and %d do not overlap", i, i+1)
		}
		if n := overlapLen(chunks[i].Text, chunks[i+1].Text); n > OverlapChars {
			t.Errorf("overlap %d > %d", n, OverlapChars)
		}
	}
}

func TestCoverageEveryUnitAppears(t *testing.T) {
	var paras []string
	for i := 0; i < 50; i++ {
		paras = append(paras, para(i*4, 4))
	}
	chunks := Document(filing, parse.Document{Sections: []parse.Section{mkSection("7", 1, paras...)}}, Options{})
	var all strings.Builder
	for _, c := range chunks {
		all.WriteString(c.Text)
		all.WriteString("\n")
	}
	for i := 0; i < 200; i++ {
		if !strings.Contains(all.String(), fmt.Sprintf("Sentence number %d describes", i)) {
			t.Fatalf("sentence %d lost", i)
		}
	}
}

func TestLongBlockWithoutSentencesIsStillBounded(t *testing.T) {
	long := strings.Repeat("word ", 3000) // 15k chars, no sentence ends
	glued := strings.Repeat("x", 5000)    // no spaces at all
	doc := parse.Document{Sections: []parse.Section{mkSection("1", 1, long, glued)}}
	for i, c := range Document(filing, doc, Options{}) {
		if len(c.Text) > MaxChars {
			t.Errorf("chunk %d has %d chars", i, len(c.Text))
		}
	}
}

func TestSkipsCoverAndExhibits(t *testing.T) {
	doc := parse.Document{Sections: []parse.Section{
		mkSection("0", 1, para(0, 3)), mkSection("1", 2, para(10, 3)), mkSection("15", 3, para(20, 3)), mkSection("8", 4, para(30, 3)),
	}}
	var items []string
	for _, c := range Document(filing, doc, Options{}) {
		items = append(items, c.Section)
	}
	if strings.Join(items, ",") != "Item 1,Item 8" {
		t.Errorf("items = %v", items)
	}
	custom := Document(filing, doc, Options{SkipItems: map[string]bool{"1": true}})
	if len(custom) != 3 {
		t.Errorf("custom skip: %d chunks", len(custom))
	}
}

func TestMetadataCompletenessAndHash(t *testing.T) {
	doc := parse.Document{Sections: []parse.Section{mkSection("7A", 9, para(0, 4)), {Item: "8", Title: "Statements", Synthetic: true, Blocks: []parse.Block{{Text: para(50, 3), Page: 12}}}}}
	chunks := Document(filing, doc, Options{})
	seen := map[string]bool{}
	for _, c := range chunks {
		if c.Filing != filing || c.Section == "" || c.SectionTitle == "" || c.ContextHeader == "" || c.Text == "" || c.StartPage == 0 || c.EndPage < c.StartPage {
			t.Errorf("incomplete chunk: %+v", c)
		}
		if !strings.Contains(c.ContextHeader, "Acme Corp") || !strings.Contains(c.ContextHeader, "FY2025") || !strings.Contains(c.ContextHeader, c.Section) {
			t.Errorf("header = %q", c.ContextHeader)
		}
		if c.Tokens() <= 0 || len(c.Hash()) != 64 {
			t.Errorf("tokens/hash broken: %d %q", c.Tokens(), c.Hash())
		}
		if seen[c.Hash()] {
			t.Error("duplicate hash for distinct chunk")
		}
		seen[c.Hash()] = true
	}
	if !chunks[len(chunks)-1].Synthetic || chunks[0].Synthetic {
		t.Error("synthetic flag not propagated")
	}
	if again := Document(filing, doc, Options{}); again[0].Hash() != chunks[0].Hash() {
		t.Error("hash not deterministic")
	}
}

func TestPagesTracked(t *testing.T) {
	var blocks []parse.Block
	for p := 1; p <= 10; p++ {
		blocks = append(blocks, parse.Block{Text: para(p*10, 6), Page: p})
	}
	chunks := Document(filing, parse.Document{Sections: []parse.Section{{Item: "7", Title: "MD&A", Blocks: blocks}}}, Options{})
	prev := 0
	for _, c := range chunks {
		if c.StartPage < prev || c.EndPage < c.StartPage {
			t.Errorf("pages not monotone: %+v", c)
		}
		prev = c.StartPage
	}
	if chunks[0].StartPage != 1 || chunks[len(chunks)-1].EndPage != 10 {
		t.Errorf("page range %d-%d", chunks[0].StartPage, chunks[len(chunks)-1].EndPage)
	}
}

func TestTinySectionsAndTailMerge(t *testing.T) {
	small := Document(filing, parse.Document{Sections: []parse.Section{mkSection("1B", 1, "None.")}}, Options{})
	if len(small) != 1 || small[0].Text != "None." {
		t.Errorf("tiny section: %+v", small)
	}
	if got := Document(filing, parse.Document{}, Options{}); len(got) != 0 {
		t.Errorf("empty doc: %v", got)
	}
}
