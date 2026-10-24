// Command sqlguard runs the SQL validation service.
package main

import (
	"context"
	"errors"
	"flag"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	sqlguard "github.com/vasusadariya/finrag/sqlguard-go"
)

func getenv(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

// probe is the container health check: the image has no curl, so the binary checks itself.
func probe(addr string) int {
	host, port, err := net.SplitHostPort(addr)
	if err != nil {
		return 1
	}
	if host == "" || host == "0.0.0.0" {
		host = "127.0.0.1"
	}
	client := &http.Client{Timeout: 2 * time.Second}
	resp, err := client.Get("http://" + net.JoinHostPort(host, port) + "/healthz")
	if err != nil {
		return 1
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return 1
	}
	return 0
}

func main() {
	addr := flag.String("addr", getenv("SQLGUARD_ADDR", "127.0.0.1:8081"), "listen address")
	policyPath := flag.String("policy", getenv("SQLGUARD_POLICY", "db/sql_policy.json"), "path to sql_policy.json")
	healthcheck := flag.Bool("healthcheck", false, "probe /healthz on -addr and exit 0 if healthy (for container health checks)")
	flag.Parse()

	if *healthcheck {
		os.Exit(probe(*addr))
	}

	policy, err := sqlguard.LoadPolicy(*policyPath)
	if err != nil {
		log.Fatalf("sqlguard: %v", err)
	}
	srv := &http.Server{
		Addr:              *addr,
		Handler:           sqlguard.Handler(sqlguard.Validator{P: policy}),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      10 * time.Second,
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = srv.Shutdown(shutdown)
	}()
	log.Printf("sqlguard listening on %s (policy %s, %d tables)", *addr, *policyPath, len(policy.Tables))
	if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatalf("sqlguard: %v", err)
	}
}
