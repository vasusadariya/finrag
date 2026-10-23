// Command sqlguard runs the SQL validation service.
package main

import (
	"context"
	"errors"
	"flag"
	"log"
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

func main() {
	addr := flag.String("addr", getenv("SQLGUARD_ADDR", "127.0.0.1:8081"), "listen address")
	policyPath := flag.String("policy", getenv("SQLGUARD_POLICY", "db/sql_policy.json"), "path to sql_policy.json")
	flag.Parse()

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
