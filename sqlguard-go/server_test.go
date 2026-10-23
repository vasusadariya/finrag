package sqlguard

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func post(t *testing.T, h http.Handler, body string) (*httptest.ResponseRecorder, Result) {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/validate", strings.NewReader(body))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	var res Result
	_ = json.Unmarshal(rec.Body.Bytes(), &res)
	return rec, res
}

func TestHTTPValidateAcceptsAndRejects(t *testing.T) {
	h := Handler(testValidator(t))
	rec, res := post(t, h, `{"sql": "SELECT ticker FROM companies"}`)
	if rec.Code != 200 || !res.OK || res.SQL != "SELECT ticker FROM companies LIMIT 50" {
		t.Fatalf("%d %+v", rec.Code, res)
	}
	rec, res = post(t, h, `{"sql": "DROP TABLE companies"}`)
	if rec.Code != 200 || res.OK || len(res.Reasons) == 0 || res.SQL != "" {
		t.Fatalf("rejection must be a 200 with ok=false: %d %+v", rec.Code, res)
	}
	if ct := rec.Header().Get("Content-Type"); ct != "application/json" {
		t.Errorf("content-type %q", ct)
	}
}

func TestHTTPBadRequests(t *testing.T) {
	h := Handler(testValidator(t))
	for body, want := range map[string]int{
		`not json`:                           400,
		`{}`:                                 400,
		`{"sql": 5}`:                         400,
		`{"sql": "SELECT 1", "extra": true}`: 400,
		``:                                   400,
		`{"sql": "` + strings.Repeat("a", 20000) + `"}`: 413,
	} {
		if rec, _ := post(t, h, body); rec.Code != want {
			t.Errorf("%.30q: got %d, want %d", body, rec.Code, want)
		}
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/validate", nil))
	if rec.Code != 405 || rec.Header().Get("Allow") != "POST" {
		t.Errorf("GET /validate: %d", rec.Code)
	}
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/nope", nil))
	if rec.Code != 404 {
		t.Errorf("unknown path: %d", rec.Code)
	}
}

func TestHTTPHealth(t *testing.T) {
	srv := httptest.NewServer(Handler(testValidator(t)))
	defer srv.Close()
	resp, err := http.Get(srv.URL + "/healthz")
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = resp.Body.Close() }()
	b, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 || !strings.Contains(string(b), "ok") {
		t.Errorf("%d %s", resp.StatusCode, b)
	}
}
