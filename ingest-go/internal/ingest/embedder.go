package ingest

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"

	"github.com/vasusadariya/finrag/ingest-go/internal/chunk"
	"github.com/vasusadariya/finrag/ingest-go/internal/load"
)

// Embedder turns chunks into vectors keyed by chunk.Hash().
type Embedder interface {
	Embed(ctx context.Context, chunks []chunk.Chunk) (map[string][]float32, error)
}

// PythonEmbedder runs the backend's embedding job (python -m app.embed.job) as a subprocess:
// JSONL {"hash","text"} on stdin, JSONL {"hash","embedding"} on stdout. Vectors are cached on disk by the job.
type PythonEmbedder struct {
	Dir     string   // working directory (the backend project)
	Command []string // default: uv run --group embed python -m app.embed.job --cache <Cache>
	Cache   string
}

func (e PythonEmbedder) command() []string {
	if len(e.Command) > 0 {
		return e.Command
	}
	return []string{"uv", "run", "--group", "embed", "python", "-m", "app.embed.job", "--cache", e.Cache}
}

func (e PythonEmbedder) Embed(ctx context.Context, chunks []chunk.Chunk) (map[string][]float32, error) {
	var in bytes.Buffer
	enc := json.NewEncoder(&in)
	for _, c := range chunks {
		if err := enc.Encode(map[string]string{"hash": c.Hash(), "text": c.EmbedText()}); err != nil {
			return nil, err
		}
	}
	argv := e.command()
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	cmd.Dir = e.Dir
	cmd.Stdin = &in
	var out bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		return nil, fmt.Errorf("ingest: embed job failed: %w", err)
	}
	return load.ReadEmbeddings(&out)
}
