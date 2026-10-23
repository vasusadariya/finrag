// Package chunk splits parsed filing sections into overlapping, metadata-tagged chunks for retrieval.
package chunk

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"strings"
	"unicode"

	"github.com/vasusadariya/finrag/ingest-go/internal/parse"
)

// Sizes are in characters; tokens are estimated at 4 chars/token (no tokenizer dependency).
// Defaults target the 512-token window of the local bge embedding model.
const (
	CharsPerToken = 4
	MaxChars      = 1500 // hard ceiling per chunk, including overlap
	OverlapChars  = 250  // carried into the next chunk
	unitMaxChars  = 400  // blocks longer than this are split at sentence boundaries
	minTailChars  = 500  // a shorter final piece is merged into its predecessor
)

// Filing identifies the document being chunked.
type Filing struct {
	Ticker     string
	Company    string
	FiscalYear int
	Form       string
	Accession  string
}

// Chunk is one retrievable passage.
type Chunk struct {
	Filing        Filing
	Section       string // "Item 1A"
	SectionTitle  string
	Synthetic     bool // section was carved from a trailing region (see parse.Section)
	Index         int  // 0-based position within the filing; unique per filing
	Text          string
	StartPage     int
	EndPage       int
	ContextHeader string
}

// Tokens returns the estimated token count of Text.
func (c Chunk) Tokens() int { return (len(c.Text) + CharsPerToken - 1) / CharsPerToken }

// EmbedText is what gets embedded: the passage prefixed with where it comes from.
func (c Chunk) EmbedText() string { return c.ContextHeader + "\n" + c.Text }

// Hash is a stable content hash of EmbedText, used as the embedding cache key.
func (c Chunk) Hash() string {
	sum := sha256.Sum256([]byte(c.EmbedText()))
	return hex.EncodeToString(sum[:])
}

// Options tunes chunking; the zero value uses the defaults.
type Options struct {
	SkipItems map[string]bool // default: cover/TOC "0" and exhibit lists "15", "16"
}

func (o Options) skip(item string) bool {
	if o.SkipItems != nil {
		return o.SkipItems[item]
	}
	return item == "0" || item == "15" || item == "16"
}

// Document chunks every non-skipped section of a parsed filing.
func Document(f Filing, doc parse.Document, opt Options) []Chunk {
	var out []Chunk
	for _, s := range doc.Sections {
		if opt.skip(s.Item) {
			continue
		}
		header := fmt.Sprintf("%s (%s) %s FY%d | Item %s %s", f.Company, f.Ticker, f.Form, f.FiscalYear, s.Item, s.Title)
		for _, c := range section(s) {
			c.Filing = f
			c.Section = "Item " + s.Item
			c.SectionTitle = s.Title
			c.Synthetic = s.Synthetic
			c.ContextHeader = header
			c.Index = len(out)
			out = append(out, c)
		}
	}
	return out
}

type unit struct {
	text string
	page int
}

// units flattens blocks, splitting long ones at sentence boundaries.
func units(blocks []parse.Block) []unit {
	var us []unit
	for _, b := range blocks {
		for _, piece := range splitSentences(b.Text, unitMaxChars) {
			us = append(us, unit{piece, b.Page})
		}
	}
	return us
}

// splitSentences breaks text into pieces of at most max chars, preferring ". " boundaries, then spaces.
func splitSentences(text string, max int) []string {
	var out []string
	for len(text) > max {
		cut := strings.LastIndexAny(text[:max], ".!?;")
		for cut > 0 && (cut+1 >= len(text) || !unicode.IsSpace(rune(text[cut+1]))) {
			cut = strings.LastIndexAny(text[:cut], ".!?;")
		}
		if cut < max/3 { // no usable sentence end: split on a space
			cut = strings.LastIndexByte(text[:max], ' ')
			if cut < max/3 {
				cut = max - 1 // pathological token with no spaces
			}
		}
		out = append(out, strings.TrimSpace(text[:cut+1]))
		text = strings.TrimSpace(text[cut+1:])
	}
	if text != "" {
		out = append(out, text)
	}
	return out
}

func section(s parse.Section) []Chunk {
	us := units(s.Blocks)
	var chunks []Chunk
	for i := 0; i < len(us); {
		start := i
		size := 0
		for i < len(us) {
			add := len(us[i].text)
			if i > start {
				add++ // newline separator
			}
			if size+add > MaxChars && i > start {
				break
			}
			size += add
			i++
		}
		chunks = append(chunks, build(us[start:i]))
		if i >= len(us) {
			break
		}
		// overlap: step back over trailing units totalling at most OverlapChars, but always make progress
		back, carried := i, 0
		for back > start+1 && carried+len(us[back-1].text)+1 <= OverlapChars {
			back--
			carried += len(us[back].text) + 1
		}
		i = back
	}
	return mergeTail(chunks)
}

func build(us []unit) Chunk {
	parts := make([]string, len(us))
	for i, u := range us {
		parts[i] = u.text
	}
	return Chunk{Text: strings.Join(parts, "\n"), StartPage: us[0].page, EndPage: us[len(us)-1].page}
}

// mergeTail folds a very short final chunk into its predecessor when the result still fits.
func mergeTail(chunks []Chunk) []Chunk {
	n := len(chunks)
	if n < 2 || len(chunks[n-1].Text) >= minTailChars {
		return chunks
	}
	prev, last := chunks[n-2], chunks[n-1]
	// the tail overlaps its predecessor, so only the non-overlapping suffix is new text
	merged := prev.Text + "\n" + last.Text
	if i := overlapLen(prev.Text, last.Text); i > 0 {
		merged = prev.Text + last.Text[i:]
	}
	if len(merged) > MaxChars {
		return chunks
	}
	prev.Text, prev.EndPage = merged, last.EndPage
	return append(chunks[:n-2], prev)
}

// overlapLen returns the length of the longest prefix of b that is a suffix of a (0 if none).
func overlapLen(a, b string) int {
	max := min(len(a), len(b), OverlapChars)
	for n := max; n > 0; n-- {
		if strings.HasSuffix(a, b[:n]) && (n == len(b) || b[n] == '\n') {
			return n
		}
	}
	return 0
}
