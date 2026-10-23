package ingest

import (
	"bytes"
	"context"
	"database/sql"
	"fmt"
	"slices"

	"github.com/vasusadariya/finrag/ingest-go/internal/chunk"
	"github.com/vasusadariya/finrag/ingest-go/internal/edgar"
	"github.com/vasusadariya/finrag/ingest-go/internal/load"
	"github.com/vasusadariya/finrag/ingest-go/internal/parse"
	"github.com/vasusadariya/finrag/ingest-go/internal/xbrl"
)

type Pipeline struct {
	EDGAR     *edgar.Client
	Endpoints edgar.Endpoints
	Loader    load.Loader
	Embedder  Embedder
	Years     int
	Logf      func(format string, args ...any)
}

type FilingReport struct {
	Accession  string
	FiscalYear int
	Chunks     int
	Skipped    bool // already up to date in the database
}

type CompanyReport struct {
	Ticker  string
	Filings []FilingReport
	Facts   int
}

func (p Pipeline) logf(format string, args ...any) {
	if p.Logf != nil {
		p.Logf(format, args...)
	}
}

// RunCompany ingests the latest p.Years 10-Ks and the XBRL facts of one company. It is safe to re-run: filings
// whose stored chunks already match are skipped, and everything else is upserted.
func (p Pipeline) RunCompany(ctx context.Context, c Company) (CompanyReport, error) {
	rep := CompanyReport{Ticker: c.Ticker}
	filings, err := p.EDGAR.LatestTenKs(ctx, p.Endpoints, c.CIK, p.Years)
	if err != nil {
		return rep, fmt.Errorf("%s: list filings: %w", c.Ticker, err)
	}
	if len(filings) == 0 {
		return rep, fmt.Errorf("%s: no 10-K filings found", c.Ticker)
	}
	urls := make([]string, len(filings))
	for i, f := range filings {
		urls[i] = p.Endpoints.DocumentURL(f)
	}
	docs := p.EDGAR.GetMany(ctx, urls, 3)

	companyID, err := p.Loader.UpsertCompany(ctx, load.Company{CIK: c.CIK, Ticker: c.Ticker, Name: c.Name})
	if err != nil {
		return rep, err
	}
	filingIDs := map[string]int{}
	for i, f := range filings {
		if docs[i].Err != nil {
			return rep, fmt.Errorf("%s %s: download: %w", c.Ticker, f.Accession, docs[i].Err)
		}
		fr, id, err := p.ingestFiling(ctx, c, companyID, f, urls[i], docs[i].Body)
		if err != nil {
			return rep, fmt.Errorf("%s %s: %w", c.Ticker, f.Accession, err)
		}
		filingIDs[f.Accession] = id
		rep.Filings = append(rep.Filings, fr)
	}

	body, err := p.EDGAR.FetchCompanyFacts(ctx, p.Endpoints, c.CIK)
	if err != nil {
		return rep, fmt.Errorf("%s: companyfacts: %w", c.Ticker, err)
	}
	facts, err := xbrl.Parse(body)
	if err != nil {
		return rep, fmt.Errorf("%s: %w", c.Ticker, err)
	}
	if err := p.Loader.ReplaceFacts(ctx, companyID, facts, filingIDs); err != nil {
		return rep, err
	}
	rep.Facts = len(facts)
	p.logf("%s: %d XBRL facts", c.Ticker, len(facts))
	return rep, nil
}

func (p Pipeline) ingestFiling(ctx context.Context, c Company, companyID int, f edgar.Filing, url string, raw []byte) (FilingReport, int, error) {
	meta := parse.ExtractMeta(raw)
	fy := meta.FiscalYear
	if fy == 0 {
		fy = f.FiscalYear
	}
	fr := FilingReport{Accession: f.Accession, FiscalYear: fy}
	filingID, err := p.Loader.UpsertFiling(ctx, load.Filing{CompanyID: companyID, Accession: f.Accession, Form: f.Form,
		FiscalYear: fy, PeriodEnd: f.PeriodEnd, FiledAt: f.FiledAt, URL: url})
	if err != nil {
		return fr, 0, err
	}
	blocks, err := parse.ExtractBlocks(bytes.NewReader(raw))
	if err != nil {
		return fr, 0, err
	}
	doc := parse.Split(blocks)
	if err := doc.CheckPlausible(); err != nil {
		return fr, 0, fmt.Errorf("refusing to load: %w", err)
	}
	for _, w := range doc.Warnings {
		p.logf("%s %s: parse warning: %s", c.Ticker, f.Accession, w)
	}
	name := meta.Registrant
	if name == "" {
		name = c.Name
	}
	chunks := chunk.Document(chunk.Filing{Ticker: c.Ticker, Company: name, FiscalYear: fy, Form: f.Form, Accession: f.Accession}, doc, chunk.Options{})
	fr.Chunks = len(chunks)

	stored, err := storedHashes(ctx, p.Loader.DB, filingID)
	if err != nil {
		return fr, 0, err
	}
	want := make([]string, len(chunks))
	for i, ch := range chunks {
		want[i] = ch.Hash()
	}
	if slices.Equal(stored, want) {
		fr.Skipped = true
		p.logf("%s FY%d: up to date (%d chunks)", c.Ticker, fy, len(chunks))
		return fr, filingID, nil
	}
	p.logf("%s FY%d: embedding %d chunks", c.Ticker, fy, len(chunks))
	emb, err := p.Embedder.Embed(ctx, chunks)
	if err != nil {
		return fr, 0, err
	}
	if err := p.Loader.ReplaceChunks(ctx, companyID, filingID, chunks, emb); err != nil {
		return fr, 0, err
	}
	p.logf("%s FY%d: loaded %d chunks", c.Ticker, fy, len(chunks))
	return fr, filingID, nil
}

func storedHashes(ctx context.Context, db *sql.DB, filingID int) ([]string, error) {
	rows, err := db.QueryContext(ctx, `SELECT content_hash FROM chunks WHERE filing_id = $1 ORDER BY chunk_index`, filingID)
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()
	var out []string
	for rows.Next() {
		var h string
		if err := rows.Scan(&h); err != nil {
			return nil, err
		}
		out = append(out, h)
	}
	return out, rows.Err()
}

// Counts is a row-count summary of the database.
type Counts struct {
	Ticker  string
	Filings int
	Chunks  int
	Facts   int
}

// RowCounts reports per-company filings, chunks and facts.
func RowCounts(ctx context.Context, db *sql.DB) ([]Counts, error) {
	rows, err := db.QueryContext(ctx, `
SELECT c.ticker,
  (SELECT count(*) FROM filings f WHERE f.company_id = c.id),
  (SELECT count(*) FROM chunks k WHERE k.company_id = c.id),
  (SELECT count(*) FROM xbrl_facts x WHERE x.company_id = c.id)
FROM companies c ORDER BY c.ticker`)
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()
	var out []Counts
	for rows.Next() {
		var c Counts
		if err := rows.Scan(&c.Ticker, &c.Filings, &c.Chunks, &c.Facts); err != nil {
			return nil, err
		}
		out = append(out, c)
	}
	return out, rows.Err()
}
