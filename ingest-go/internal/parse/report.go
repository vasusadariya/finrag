package parse

import (
	"fmt"
	"strings"
)

// Report renders a human-checkable table of section boundaries.
func (d Document) Report() string {
	var sb strings.Builder
	fmt.Fprintf(&sb, "%-5s %-9s %9s %5s  %s\n", "ITEM", "PAGES", "CHARS", "BLKS", "TITLE / FIRST WORDS")
	for _, s := range d.Sections {
		synth := ""
		if s.Synthetic {
			synth = " [synthetic]"
		}
		first := s.Blocks[0].Text
		if len(s.Blocks) > 1 {
			first = s.Blocks[1].Text
		}
		if len(first) > 70 {
			first = first[:70] + "…"
		}
		fmt.Fprintf(&sb, "%-5s %4d-%-4d %9d %5d  %s%s | %s\n", s.Item, s.StartPage(), s.EndPage(), s.chars(), len(s.Blocks), truncate(s.Title, 40), synth, first)
	}
	for _, w := range d.Warnings {
		fmt.Fprintf(&sb, "WARN: %s\n", w)
	}
	return sb.String()
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}
