// Command finrag-ingest downloads, parses, chunks and loads SEC filings.
package main

import (
	"fmt"
	"os"

	"github.com/vasusadariya/finrag/ingest-go/internal/parse"
)

func main() {
	if len(os.Args) == 3 && os.Args[1] == "parse" {
		if err := parseReport(os.Args[2]); err != nil {
			fmt.Fprintln(os.Stderr, "error:", err)
			os.Exit(1)
		}
		return
	}
	fmt.Fprintln(os.Stderr, "usage: finrag-ingest parse <filing.htm>")
	os.Exit(2)
}

func parseReport(path string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer func() { _ = f.Close() }()
	blocks, err := parse.ExtractBlocks(f)
	if err != nil {
		return err
	}
	fmt.Print(parse.Split(blocks).Report())
	return nil
}
