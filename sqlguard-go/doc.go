// Package sqlguard validates LLM-written SQL before it reaches Postgres.
//
// It parses the statement with the real PostgreSQL parser (libpg_query) and applies a default-deny policy over
// the parse tree: only a small set of read-only node types is allowed, only allow-listed tables, columns and
// functions may be referenced, and a LIMIT is enforced. The returned SQL is deparsed from the validated tree,
// so what is executed is exactly what was checked.
package sqlguard
