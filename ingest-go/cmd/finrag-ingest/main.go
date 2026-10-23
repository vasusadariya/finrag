// Command finrag-ingest downloads, parses, chunks, embeds and loads SEC filings and XBRL facts.
package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"text/tabwriter"

	_ "github.com/jackc/pgx/v5/stdlib"

	"github.com/vasusadariya/finrag/ingest-go/internal/chunk"
	"github.com/vasusadariya/finrag/ingest-go/internal/edgar"
	"github.com/vasusadariya/finrag/ingest-go/internal/ingest"
	"github.com/vasusadariya/finrag/ingest-go/internal/load"
	"github.com/vasusadariya/finrag/ingest-go/internal/parse"
)

const usage = `usage:
  finrag-ingest run [--tickers AAPL,MSFT] [--years 3]   download, parse, chunk, embed and load (resumable)
  finrag-ingest report                                  row counts per company
  finrag-ingest parse <filing.htm>                      print the section-boundary report for a filing
  finrag-ingest chunks <TICKER> <filing.htm>            print a filing's chunks as JSON lines`

func main() {
	loadDotEnv(".env", filepath.Join("..", ".env"))
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, usage)
		os.Exit(2)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	var err error
	switch os.Args[1] {
	case "run":
		err = runCmd(ctx, os.Args[2:])
	case "report":
		err = reportCmd(ctx)
	case "parse":
		if len(os.Args) != 3 {
			fmt.Fprintln(os.Stderr, usage)
			os.Exit(2)
		}
		err = parseReport(os.Args[2])
	case "chunks":
		if len(os.Args) != 4 {
			fmt.Fprintln(os.Stderr, usage)
			os.Exit(2)
		}
		err = dumpChunks(os.Args[2], os.Args[3])
	default:
		fmt.Fprintln(os.Stderr, usage)
		os.Exit(2)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}

// loadDotEnv sets variables from the first readable .env file without overriding the real environment.
func loadDotEnv(paths ...string) {
	for _, p := range paths {
		b, err := os.ReadFile(p)
		if err != nil {
			continue
		}
		for _, line := range strings.Split(string(b), "\n") {
			line = strings.TrimSpace(line)
			k, v, ok := strings.Cut(line, "=")
			if line == "" || strings.HasPrefix(line, "#") || !ok {
				continue
			}
			k, v = strings.TrimSpace(k), strings.Trim(strings.TrimSpace(v), `"'`)
			if _, set := os.LookupEnv(k); !set {
				_ = os.Setenv(k, v)
			}
		}
		return
	}
}

func openDB(url string) (*sql.DB, error) {
	if url == "" {
		return nil, fmt.Errorf("DATABASE_URL is not set (see .env.example)")
	}
	db, err := sql.Open("pgx", url)
	if err != nil {
		return nil, err
	}
	if err := db.Ping(); err != nil {
		return nil, fmt.Errorf("cannot reach database: %w", err)
	}
	return db, nil
}

func runCmd(ctx context.Context, args []string) error {
	fs := flag.NewFlagSet("run", flag.ExitOnError)
	tickers := fs.String("tickers", "", "comma-separated tickers (default: all 8)")
	years := fs.Int("years", 3, "number of most recent 10-Ks per company")
	cacheDir := fs.String("cache-dir", filepath.Join("..", ".cache", "edgar"), "on-disk cache for EDGAR downloads")
	embedCache := fs.String("embed-cache", "", "embedding cache file (default <repo>/.cache/embeddings.sqlite)")
	backendDir := fs.String("backend-dir", filepath.Join("..", "backend"), "backend project used for the embedding job")
	if err := fs.Parse(args); err != nil {
		return err
	}
	ua := os.Getenv("EDGAR_USER_AGENT")
	if ua == "" {
		return fmt.Errorf("EDGAR_USER_AGENT is not set (SEC requires a contact in the User-Agent)")
	}
	companies, unknown := ingest.Select(*tickers)
	if len(unknown) > 0 {
		return fmt.Errorf("unknown tickers: %s", strings.Join(unknown, ", "))
	}
	db, err := openDB(os.Getenv("DATABASE_URL"))
	if err != nil {
		return err
	}
	defer func() { _ = db.Close() }()
	client, err := edgar.New(edgar.Config{UserAgent: ua, CacheDir: *cacheDir})
	if err != nil {
		return err
	}
	cache := *embedCache
	if cache == "" {
		cache, _ = filepath.Abs(filepath.Join("..", ".cache", "embeddings.sqlite"))
	}
	if err := os.MkdirAll(filepath.Dir(cache), 0o755); err != nil {
		return err
	}
	p := ingest.Pipeline{
		EDGAR: client, Endpoints: edgar.DefaultEndpoints, Loader: load.Loader{DB: db},
		Embedder: ingest.PythonEmbedder{Dir: *backendDir, Cache: cache},
		Years:    *years,
		Logf:     func(f string, a ...any) { fmt.Fprintf(os.Stderr, f+"\n", a...) },
	}
	var failed []string
	for _, c := range companies {
		if ctx.Err() != nil {
			break
		}
		fmt.Fprintf(os.Stderr, "== %s (%s)\n", c.Ticker, c.Name)
		if _, err := p.RunCompany(ctx, c); err != nil {
			fmt.Fprintf(os.Stderr, "FAILED %s: %v\n", c.Ticker, err)
			failed = append(failed, c.Ticker)
		}
	}
	if err := printCounts(ctx, db); err != nil {
		return err
	}
	if ctx.Err() != nil {
		return fmt.Errorf("interrupted; re-run to resume")
	}
	if len(failed) > 0 {
		return fmt.Errorf("%d company(ies) failed: %s (re-run to resume)", len(failed), strings.Join(failed, ", "))
	}
	return nil
}

func reportCmd(ctx context.Context) error {
	db, err := openDB(os.Getenv("DATABASE_URL"))
	if err != nil {
		return err
	}
	defer func() { _ = db.Close() }()
	return printCounts(ctx, db)
}

func printCounts(ctx context.Context, db *sql.DB) error {
	counts, err := ingest.RowCounts(ctx, db)
	if err != nil {
		return err
	}
	w := tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', tabwriter.AlignRight)
	_, _ = fmt.Fprintln(w, "TICKER\tFILINGS\tCHUNKS\tFACTS\t")
	var f, c, x int
	for _, r := range counts {
		_, _ = fmt.Fprintf(w, "%s\t%d\t%d\t%d\t\n", r.Ticker, r.Filings, r.Chunks, r.Facts)
		f, c, x = f+r.Filings, c+r.Chunks, x+r.Facts
	}
	_, _ = fmt.Fprintf(w, "TOTAL\t%d\t%d\t%d\t\n", f, c, x)
	return w.Flush()
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
