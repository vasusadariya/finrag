package sqlguard

import (
	"encoding/json"
	"errors"
	"net/http"
)

const maxRequestBytes = 16 << 10

// Handler serves the validator over HTTP:
//
//	POST /validate  {"sql": "..."}  ->  {"ok": bool, "sql": "...", "reasons": [...]}
//	GET  /healthz                   ->  {"status": "ok"}
//
// A rejected query is a normal 200 response with ok=false; non-200 means the request itself was bad.
func Handler(v Validator) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "use GET"})
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
	})
	mux.HandleFunc("/validate", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			w.Header().Set("Allow", http.MethodPost)
			writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "use POST"})
			return
		}
		r.Body = http.MaxBytesReader(w, r.Body, maxRequestBytes)
		var req struct {
			SQL *string `json:"sql"`
		}
		dec := json.NewDecoder(r.Body)
		dec.DisallowUnknownFields()
		if err := dec.Decode(&req); err != nil {
			var tooBig *http.MaxBytesError
			if errors.As(err, &tooBig) {
				writeJSON(w, http.StatusRequestEntityTooLarge, map[string]string{"error": "request too large"})
				return
			}
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "body must be JSON like {\"sql\": \"...\"}"})
			return
		}
		if req.SQL == nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "missing field: sql"})
			return
		}
		writeJSON(w, http.StatusOK, v.Validate(*req.SQL))
	})
	return mux
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}
