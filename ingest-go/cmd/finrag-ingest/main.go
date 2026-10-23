// Command finrag-ingest downloads, parses, chunks and loads SEC filings.
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"

	"github.com/vasusadariya/finrag/ingest-go/internal/chunk"
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
	if len(os.Args) == 4 && os.Args[1] == "chunks" {
		if err := dumpChunks(os.Args[2], os.Args[3]); err != nil {
			fmt.Fprintln(os.Stderr, "error:", err)
			os.Exit(1)
		}
		return
	}
	fmt.Fprintln(os.Stderr, "usage: finrag-ingest parse <filing.htm> | chunks <TICKER> <filing.htm>")
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

// dumpChunks prints a filing's chunks as JSON lines (debugging and embedding benchmarks).
func dumpChunks(ticker, path string) error {
	raw, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	blocks, err := parse.ExtractBlocks(bytes.NewReader(raw))
	if err != nil {
		return err
	}
	meta := parse.ExtractMeta(raw)
	f := chunk.Filing{Ticker: ticker, Company: meta.Registrant, FiscalYear: meta.FiscalYear, Form: "10-K"}
	enc := json.NewEncoder(os.Stdout)
	for _, c := range chunk.Document(f, parse.Split(blocks), chunk.Options{}) {
		if err := enc.Encode(map[string]any{"hash": c.Hash(), "section": c.Section, "page": c.StartPage, "text": c.EmbedText()}); err != nil {
			return err
		}
	}
	return nil
}
