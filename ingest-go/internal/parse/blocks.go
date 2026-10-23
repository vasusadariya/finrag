// Package parse turns SEC filing HTML into page-tracked text blocks and Item-level sections.
package parse

import (
	"fmt"
	"io"
	"regexp"
	"strings"

	"golang.org/x/net/html"
)

// Block is one paragraph-like unit of text with the physical page it appeared on (1-based).
type Block struct {
	Text string
	Page int
}

var blockTags = map[string]bool{
	"div": true, "p": true, "tr": true, "li": true, "br": true, "hr": true, "table": true,
	"h1": true, "h2": true, "h3": true, "h4": true, "h5": true, "h6": true,
	"section": true, "ul": true, "ol": true, "body": true,
}

var wsRun = regexp.MustCompile(`[\s\x{00a0}\x{200b}]+`)

type extractor struct {
	blocks []Block
	buf    strings.Builder
	page   int
}

func (x *extractor) flush() {
	if x.buf.Len() == 0 {
		return
	}
	text := strings.TrimSpace(wsRun.ReplaceAllString(x.buf.String(), " "))
	x.buf.Reset()
	if text != "" {
		x.blocks = append(x.blocks, Block{Text: text, Page: x.page})
	}
}

func styleOf(n *html.Node) string {
	for _, a := range n.Attr {
		if a.Key == "style" {
			return strings.ToLower(strings.ReplaceAll(a.Val, " ", ""))
		}
	}
	return ""
}

func breaksBefore(style string) bool {
	return strings.Contains(style, "page-break-before:always") || strings.Contains(style, "break-before:page")
}

func breaksAfter(style string) bool {
	return strings.Contains(style, "page-break-after:always") || strings.Contains(style, "break-after:page")
}

func (x *extractor) walk(n *html.Node) {
	switch n.Type {
	case html.TextNode:
		x.buf.WriteString(n.Data)
		return
	case html.ElementNode:
		switch n.Data {
		case "script", "style", "head", "ix:header":
			return
		}
	}
	style := ""
	if n.Type == html.ElementNode {
		style = styleOf(n)
		if strings.Contains(style, "display:none") {
			return
		}
	}
	isBlock := n.Type == html.ElementNode && blockTags[n.Data]
	if isBlock {
		x.flush()
	}
	if breaksBefore(style) {
		x.flush()
		x.page++
	}
	for c := n.FirstChild; c != nil; c = c.NextSibling {
		x.walk(c)
		if c.Type == html.ElementNode && (c.Data == "td" || c.Data == "th") {
			x.buf.WriteByte(' ')
		}
	}
	if isBlock {
		x.flush()
	}
	if breaksAfter(style) {
		x.flush()
		x.page++
	}
}

// ExtractBlocks parses filing HTML and returns text blocks in reading order. Hidden iXBRL headers and
// display:none content are dropped; CSS page breaks advance the page counter.
func ExtractBlocks(r io.Reader) ([]Block, error) {
	doc, err := html.Parse(r)
	if err != nil {
		return nil, fmt.Errorf("parse: html: %w", err)
	}
	x := &extractor{page: 1}
	x.walk(doc)
	x.flush()
	return DropRunningText(x.blocks), nil
}
