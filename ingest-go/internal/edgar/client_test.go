package edgar

import (
	"compress/gzip"
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func newClient(t *testing.T, cfg Config) *Client {
	t.Helper()
	if cfg.UserAgent == "" {
		cfg.UserAgent = "FinRAG test test@example.com"
	}
	if cfg.BaseDelay == 0 {
		cfg.BaseDelay = time.Millisecond
	}
	c, err := New(cfg)
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func TestRequiresUserAgent(t *testing.T) {
	if _, err := New(Config{}); err == nil {
		t.Fatal("expected error without user agent")
	}
}

func TestSendsUserAgent(t *testing.T) {
	var got string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = r.Header.Get("User-Agent")
		_, _ = w.Write([]byte("ok"))
	}))
	defer srv.Close()
	c := newClient(t, Config{UserAgent: "FinRAG me@example.com"})
	if _, err := c.Get(context.Background(), srv.URL); err != nil {
		t.Fatal(err)
	}
	if got != "FinRAG me@example.com" {
		t.Errorf("user agent = %q", got)
	}
}

func TestRetriesOn429And5xxThenSucceeds(t *testing.T) {
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch calls.Add(1) {
		case 1:
			w.WriteHeader(http.StatusTooManyRequests)
		case 2:
			w.WriteHeader(http.StatusBadGateway)
		default:
			_, _ = w.Write([]byte("done"))
		}
	}))
	defer srv.Close()
	c := newClient(t, Config{})
	b, err := c.Get(context.Background(), srv.URL)
	if err != nil || string(b) != "done" {
		t.Fatalf("got %q, %v", b, err)
	}
	if calls.Load() != 3 {
		t.Errorf("calls = %d, want 3", calls.Load())
	}
}

func TestGivesUpAfterMaxRetries(t *testing.T) {
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer srv.Close()
	c := newClient(t, Config{MaxRetries: 2})
	if _, err := c.Get(context.Background(), srv.URL); err == nil {
		t.Fatal("expected error")
	}
	if calls.Load() != 3 {
		t.Errorf("calls = %d, want 3 (1 + 2 retries)", calls.Load())
	}
}

func TestNotFoundNotRetried(t *testing.T) {
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.WriteHeader(http.StatusNotFound)
	}))
	defer srv.Close()
	c := newClient(t, Config{})
	_, err := c.Get(context.Background(), srv.URL)
	if !errors.Is(err, ErrNotFound) {
		t.Fatalf("err = %v", err)
	}
	if calls.Load() != 1 {
		t.Errorf("calls = %d", calls.Load())
	}
}

func TestHonoursRetryAfter(t *testing.T) {
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if calls.Add(1) == 1 {
			w.Header().Set("Retry-After", "1")
			w.WriteHeader(http.StatusTooManyRequests)
			return
		}
		_, _ = w.Write([]byte("x"))
	}))
	defer srv.Close()
	c := newClient(t, Config{})
	start := time.Now()
	if _, err := c.Get(context.Background(), srv.URL); err != nil {
		t.Fatal(err)
	}
	if time.Since(start) < 900*time.Millisecond {
		t.Errorf("did not wait for Retry-After: %v", time.Since(start))
	}
}

func TestDiskCache(t *testing.T) {
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		_, _ = w.Write([]byte("payload"))
	}))
	defer srv.Close()
	dir := t.TempDir()
	for range 3 {
		c := newClient(t, Config{CacheDir: dir}) // fresh client each time: cache must be on disk
		b, err := c.Get(context.Background(), srv.URL+"/a")
		if err != nil || string(b) != "payload" {
			t.Fatalf("%q %v", b, err)
		}
	}
	if calls.Load() != 1 {
		t.Errorf("server hit %d times, want 1", calls.Load())
	}
	if entries, _ := os.ReadDir(dir); len(entries) != 1 {
		t.Errorf("cache entries = %d", len(entries))
	}
}

func TestRateLimit(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte("x"))
	}))
	defer srv.Close()
	c := newClient(t, Config{RatePerSec: 20})
	urls := make([]string, 11)
	for i := range urls {
		urls[i] = fmt.Sprintf("%s/%d", srv.URL, i)
	}
	start := time.Now()
	res := c.GetMany(context.Background(), urls, 8)
	elapsed := time.Since(start)
	for _, r := range res {
		if r.Err != nil {
			t.Fatal(r.Err)
		}
	}
	// 11 requests at 20/s with burst 1 need >= 10 intervals of 50ms.
	if elapsed < 450*time.Millisecond {
		t.Errorf("rate limit not enforced: %v", elapsed)
	}
}

func TestGetManyPreservesOrderAndReportsErrors(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/missing" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		_, _ = w.Write([]byte(r.URL.Path))
	}))
	defer srv.Close()
	c := newClient(t, Config{RatePerSec: 1000})
	res := c.GetMany(context.Background(), []string{srv.URL + "/a", srv.URL + "/missing", srv.URL + "/c"}, 3)
	if string(res[0].Body) != "/a" || string(res[2].Body) != "/c" {
		t.Errorf("order broken: %+v", res)
	}
	if !errors.Is(res[1].Err, ErrNotFound) {
		t.Errorf("res[1].Err = %v", res[1].Err)
	}
}

func TestContextCancel(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer srv.Close()
	c := newClient(t, Config{BaseDelay: time.Second})
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	start := time.Now()
	if _, err := c.Get(ctx, srv.URL); err == nil {
		t.Fatal("expected error")
	}
	if time.Since(start) > 500*time.Millisecond {
		t.Error("cancel not honoured promptly")
	}
}

func TestDecodesGzipResponses(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.Contains(r.Header.Get("Accept-Encoding"), "gzip") {
			_, _ = w.Write([]byte("plain"))
			return
		}
		w.Header().Set("Content-Encoding", "gzip")
		zw := gzip.NewWriter(w)
		_, _ = zw.Write([]byte("zipped"))
		_ = zw.Close()
	}))
	defer srv.Close()
	c := newClient(t, Config{})
	b, err := c.Get(context.Background(), srv.URL)
	if err != nil || string(b) != "zipped" {
		t.Fatalf("got %q, %v", b, err)
	}
}
