// Package edgar is a polite, cached client for SEC EDGAR.
package edgar

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"sync"
	"time"

	"golang.org/x/time/rate"
)

// Config controls the client. SEC requires a descriptive User-Agent and caps traffic at 10 req/s.
type Config struct {
	UserAgent  string
	CacheDir   string        // empty disables the on-disk cache
	RatePerSec float64       // default 10
	MaxRetries int           // default 4
	BaseDelay  time.Duration // first backoff step, default 500ms
	HTTP       *http.Client  // default: 60s timeout client
}

// Client downloads URLs with rate limiting, retries and an on-disk cache.
type Client struct {
	cfg     Config
	limiter *rate.Limiter
}

// ErrNotFound is returned for HTTP 404 (not retried).
var ErrNotFound = errors.New("edgar: not found")

func New(cfg Config) (*Client, error) {
	if cfg.UserAgent == "" {
		return nil, errors.New("edgar: UserAgent is required (SEC blocks anonymous clients)")
	}
	if cfg.RatePerSec <= 0 {
		cfg.RatePerSec = 10
	}
	if cfg.MaxRetries == 0 {
		cfg.MaxRetries = 4
	}
	if cfg.BaseDelay == 0 {
		cfg.BaseDelay = 500 * time.Millisecond
	}
	if cfg.HTTP == nil {
		cfg.HTTP = &http.Client{Timeout: 60 * time.Second}
	}
	return &Client{cfg: cfg, limiter: rate.NewLimiter(rate.Limit(cfg.RatePerSec), 1)}, nil
}

func (c *Client) cachePath(url string) string {
	sum := sha256.Sum256([]byte(url))
	return filepath.Join(c.cfg.CacheDir, hex.EncodeToString(sum[:])+".bin")
}

// Get returns the body of url, using the cache when present.
func (c *Client) Get(ctx context.Context, url string) ([]byte, error) {
	if c.cfg.CacheDir != "" {
		if b, err := os.ReadFile(c.cachePath(url)); err == nil {
			return b, nil
		}
	}
	var lastErr error
	for attempt := 0; attempt <= c.cfg.MaxRetries; attempt++ {
		if attempt > 0 {
			if err := sleep(ctx, c.backoff(attempt, lastErr)); err != nil {
				return nil, err
			}
		}
		body, err := c.fetch(ctx, url)
		if err == nil {
			c.store(url, body)
			return body, nil
		}
		var re *retryableError
		if !errors.As(err, &re) {
			return nil, err
		}
		lastErr = err
	}
	return nil, fmt.Errorf("edgar: giving up on %s after %d retries: %w", url, c.cfg.MaxRetries, lastErr)
}

type retryableError struct {
	status     int
	retryAfter time.Duration
	err        error
}

func (e *retryableError) Error() string { return fmt.Sprintf("status %d: %v", e.status, e.err) }
func (e *retryableError) Unwrap() error { return e.err }

func (c *Client) backoff(attempt int, last error) time.Duration {
	var re *retryableError
	if errors.As(last, &re) && re.retryAfter > 0 {
		return re.retryAfter
	}
	return c.cfg.BaseDelay << (attempt - 1)
}

func (c *Client) fetch(ctx context.Context, url string) ([]byte, error) {
	if err := c.limiter.Wait(ctx); err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", c.cfg.UserAgent)
	req.Header.Set("Accept-Encoding", "gzip")
	resp, err := c.cfg.HTTP.Do(req)
	if err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return nil, &retryableError{err: err}
	}
	defer func() { _ = resp.Body.Close() }()
	switch {
	case resp.StatusCode == http.StatusNotFound:
		return nil, fmt.Errorf("%w: %s", ErrNotFound, url)
	case resp.StatusCode == http.StatusTooManyRequests || resp.StatusCode >= 500:
		ra := time.Duration(0)
		if s, err := strconv.Atoi(resp.Header.Get("Retry-After")); err == nil {
			ra = time.Duration(s) * time.Second
		}
		return nil, &retryableError{status: resp.StatusCode, retryAfter: ra, err: errors.New(resp.Status)}
	case resp.StatusCode != http.StatusOK:
		return nil, fmt.Errorf("edgar: %s returned %s", url, resp.Status)
	}
	b, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, &retryableError{err: err}
	}
	return b, nil
}

func (c *Client) store(url string, body []byte) {
	if c.cfg.CacheDir == "" {
		return
	}
	if err := os.MkdirAll(c.cfg.CacheDir, 0o755); err != nil {
		return
	}
	tmp := c.cachePath(url) + ".tmp" + strconv.FormatInt(time.Now().UnixNano(), 36)
	if os.WriteFile(tmp, body, 0o644) == nil {
		_ = os.Rename(tmp, c.cachePath(url))
	}
}

func sleep(ctx context.Context, d time.Duration) error {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}

// Result is one URL's outcome from GetMany.
type Result struct {
	URL  string
	Body []byte
	Err  error
}

// GetMany downloads urls with up to workers goroutines; the shared limiter still caps the request rate.
// Results are returned in input order.
func (c *Client) GetMany(ctx context.Context, urls []string, workers int) []Result {
	if workers < 1 {
		workers = 1
	}
	out := make([]Result, len(urls))
	idx := make(chan int)
	var wg sync.WaitGroup
	for range workers {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := range idx {
				b, err := c.Get(ctx, urls[i])
				out[i] = Result{URL: urls[i], Body: b, Err: err}
			}
		}()
	}
	for i := range urls {
		idx <- i
	}
	close(idx)
	wg.Wait()
	return out
}
