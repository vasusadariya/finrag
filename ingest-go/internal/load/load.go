// Package load writes companies, filings, chunks and embeddings into Postgres idempotently.
package load

import (
	"bufio"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"strconv"
	"strings"
	"time"

	"github.com/vasusadariya/finrag/ingest-go/internal/chunk"
)

// EmbeddingDim must match the vector(768) column.
const EmbeddingDim = 768

type Company struct {
	CIK    string
	Ticker string
	Name   string
}

type Filing struct {
	CompanyID  int
	Accession  string
	Form       string
	FiscalYear int
	PeriodEnd  time.Time
	FiledAt    time.Time
	URL        string
}

type Loader struct{ DB *sql.DB }

// UpsertCompany inserts or updates a company by CIK and returns its id.
func (l Loader) UpsertCompany(ctx context.Context, c Company) (int, error) {
	var id int
	err := l.DB.QueryRowContext(ctx, `
INSERT INTO companies (cik, ticker, name) VALUES ($1, $2, $3)
ON CONFLICT (cik) DO UPDATE SET ticker = EXCLUDED.ticker, name = EXCLUDED.name
RETURNING id`, c.CIK, c.Ticker, c.Name).Scan(&id)
	if err != nil {
		return 0, fmt.Errorf("load: upsert company %s: %w", c.Ticker, err)
	}
	return id, nil
}

// UpsertFiling inserts or updates a filing by accession number and returns its id.
func (l Loader) UpsertFiling(ctx context.Context, f Filing) (int, error) {
	var filed any
	if !f.FiledAt.IsZero() {
		filed = f.FiledAt
	}
	var id int
	err := l.DB.QueryRowContext(ctx, `
INSERT INTO filings (company_id, accession, form, fiscal_year, period_end, filed_at, url)
VALUES ($1, $2, $3, $4, $5, $6, $7)
ON CONFLICT (accession) DO UPDATE SET form = EXCLUDED.form, fiscal_year = EXCLUDED.fiscal_year,
  period_end = EXCLUDED.period_end, filed_at = EXCLUDED.filed_at, url = EXCLUDED.url
RETURNING id`, f.CompanyID, f.Accession, f.Form, f.FiscalYear, f.PeriodEnd, filed, f.URL).Scan(&id)
	if err != nil {
		return 0, fmt.Errorf("load: upsert filing %s: %w", f.Accession, err)
	}
	return id, nil
}

// ReplaceChunks makes the stored chunks of a filing exactly equal to chunks, in one transaction:
// rows are upserted by (filing_id, chunk_index) and any stale higher indexes are deleted, so re-ingesting
// never duplicates and a smaller re-chunking leaves no leftovers. emb maps chunk.Hash() to its vector.
func (l Loader) ReplaceChunks(ctx context.Context, companyID, filingID int, chunks []chunk.Chunk, emb map[string][]float32) error {
	vecs := make([]string, len(chunks))
	for i, c := range chunks {
		v, ok := emb[c.Hash()]
		if !ok {
			return fmt.Errorf("load: no embedding for chunk %d (%s p%d)", c.Index, c.Section, c.StartPage)
		}
		if len(v) != EmbeddingDim {
			return fmt.Errorf("load: chunk %d embedding has %d dims, want %d", c.Index, len(v), EmbeddingDim)
		}
		vecs[i] = vectorLiteral(v)
	}
	tx, err := l.DB.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback() }()
	stmt, err := tx.PrepareContext(ctx, `
INSERT INTO chunks (filing_id, company_id, fiscal_year, section, page, chunk_index, content, content_hash, token_count, embedding)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::vector)
ON CONFLICT (filing_id, chunk_index) DO UPDATE SET company_id = EXCLUDED.company_id, fiscal_year = EXCLUDED.fiscal_year,
  section = EXCLUDED.section, page = EXCLUDED.page, content = EXCLUDED.content, content_hash = EXCLUDED.content_hash,
  token_count = EXCLUDED.token_count, embedding = EXCLUDED.embedding`)
	if err != nil {
		return err
	}
	defer func() { _ = stmt.Close() }()
	for i, c := range chunks {
		if _, err := stmt.ExecContext(ctx, filingID, companyID, c.Filing.FiscalYear, c.Section, c.StartPage, c.Index,
			c.Text, c.Hash(), c.Tokens(), vecs[i]); err != nil {
			return fmt.Errorf("load: insert chunk %d: %w", c.Index, err)
		}
	}
	if _, err := tx.ExecContext(ctx, `DELETE FROM chunks WHERE filing_id = $1 AND chunk_index >= $2`, filingID, len(chunks)); err != nil {
		return err
	}
	return tx.Commit()
}

func vectorLiteral(v []float32) string {
	var sb strings.Builder
	sb.WriteByte('[')
	for i, x := range v {
		if i > 0 {
			sb.WriteByte(',')
		}
		sb.WriteString(strconv.FormatFloat(float64(x), 'g', -1, 32))
	}
	sb.WriteByte(']')
	return sb.String()
}

// ReadEmbeddings parses the embed job's JSONL output ({"hash":..., "embedding":[...]}) into a map.
func ReadEmbeddings(r io.Reader) (map[string][]float32, error) {
	out := map[string][]float32{}
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 1<<20), 16<<20)
	for n := 1; sc.Scan(); n++ {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		var row struct {
			Hash      string    `json:"hash"`
			Embedding []float32 `json:"embedding"`
		}
		if err := json.Unmarshal([]byte(line), &row); err != nil {
			return nil, fmt.Errorf("load: embeddings line %d: %w", n, err)
		}
		if row.Hash == "" || len(row.Embedding) == 0 {
			return nil, fmt.Errorf("load: embeddings line %d: missing hash or embedding", n)
		}
		out[row.Hash] = row.Embedding
	}
	return out, sc.Err()
}
