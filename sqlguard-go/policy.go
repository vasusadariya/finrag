package sqlguard

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
)

// Policy is the allow-list shared with the Python side (db/sql_policy.json).
type Policy struct {
	MaxLimit     int
	DefaultLimit int
	// Tables maps a table to its allowed columns.
	Tables    map[string]map[string]bool
	Functions map[string]bool
}

type policyFile struct {
	Version      int `json:"version"`
	MaxLimit     int `json:"max_limit"`
	DefaultLimit int `json:"default_limit"`
	Tables       map[string]struct {
		Columns map[string]string `json:"columns"`
	} `json:"tables"`
	Functions []string `json:"functions"`
}

// LoadPolicy reads and validates a policy JSON file.
func LoadPolicy(path string) (*Policy, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	return ParsePolicy(b)
}

// ParsePolicy parses policy JSON.
func ParsePolicy(b []byte) (*Policy, error) {
	var f policyFile
	if err := json.Unmarshal(b, &f); err != nil {
		return nil, fmt.Errorf("sqlguard: policy: %w", err)
	}
	if f.Version != 1 {
		return nil, fmt.Errorf("sqlguard: unsupported policy version %d", f.Version)
	}
	if len(f.Tables) == 0 {
		return nil, fmt.Errorf("sqlguard: policy has no tables")
	}
	if f.MaxLimit <= 0 || f.DefaultLimit <= 0 || f.DefaultLimit > f.MaxLimit {
		return nil, fmt.Errorf("sqlguard: invalid limits (default %d, max %d)", f.DefaultLimit, f.MaxLimit)
	}
	p := &Policy{MaxLimit: f.MaxLimit, DefaultLimit: f.DefaultLimit, Tables: map[string]map[string]bool{}, Functions: map[string]bool{}}
	for t, spec := range f.Tables {
		cols := map[string]bool{}
		for c := range spec.Columns {
			cols[strings.ToLower(c)] = true
		}
		p.Tables[strings.ToLower(t)] = cols
	}
	for _, fn := range f.Functions {
		p.Functions[strings.ToLower(fn)] = true
	}
	return p, nil
}
