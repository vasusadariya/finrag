package sqlguard

import (
	"fmt"
	"strings"

	pg_query "github.com/pganalyze/pg_query_go/v6"
	"google.golang.org/protobuf/reflect/protoreflect"
)

const (
	maxSQLBytes   = 4000
	maxNodes      = 3000
	maxSubqueries = 8
	maxReasons    = 10
)

// Result is the outcome of validating one statement.
type Result struct {
	OK      bool     `json:"ok"`
	SQL     string   `json:"sql,omitempty"` // validated, LIMIT-enforced SQL to execute (only when OK)
	Reasons []string `json:"reasons,omitempty"`
}

// allowedNodes is the default-deny list of parse-tree message types a read-only query may contain.
var allowedNodes = set(
	"Node", "SelectStmt", "ResTarget", "ColumnRef", "A_Const", "A_Expr", "A_Star", "A_ArrayExpr",
	"String", "Integer", "Float", "Boolean", "BoolExpr", "NullTest", "BooleanTest", "FuncCall",
	"TypeCast", "TypeName", "CaseExpr", "CaseWhen", "CoalesceExpr", "MinMaxExpr", "SubLink",
	"RangeVar", "RangeSubselect", "JoinExpr", "Alias", "WithClause", "CommonTableExpr", "SortBy",
	"WindowDef", "List",
)

// allowedTypes are the casts a query may use (pg_catalog names as the parser reports them).
var allowedTypes = set("int2", "int4", "int8", "numeric", "float4", "float8", "text", "varchar", "bpchar",
	"bool", "date", "timestamp", "timestamptz")

// allowedOps are the operators a query may use.
var allowedOps = set("+", "-", "*", "/", "%", "^", "=", "<>", "!=", "<", ">", "<=", ">=", "||",
	"~~", "!~~", "~~*", "!~~*", "~", "!~", "~*", "!~*")

// allowedAExpr are the A_Expr kinds a query may use (plain operators, IN, LIKE, BETWEEN, IS DISTINCT FROM...).
var allowedAExpr = set("AEXPR_OP", "AEXPR_IN", "AEXPR_LIKE", "AEXPR_ILIKE", "AEXPR_SIMILAR", "AEXPR_BETWEEN",
	"AEXPR_NOT_BETWEEN", "AEXPR_BETWEEN_SYM", "AEXPR_NOT_BETWEEN_SYM", "AEXPR_DISTINCT", "AEXPR_NOT_DISTINCT",
	"AEXPR_NULLIF", "AEXPR_OP_ANY", "AEXPR_OP_ALL")

func set(items ...string) map[string]bool {
	m := make(map[string]bool, len(items))
	for _, i := range items {
		m[i] = true
	}
	return m
}

// Validator checks SQL against a Policy.
type Validator struct{ P *Policy }

type run struct {
	p        *Policy
	reasons  []string
	seen     map[string]bool
	nodes    int
	subq     int
	ctes     map[string]bool
	tables   []string          // real tables referenced
	aliases  map[string]string // alias or table name -> table
	ambig    map[string]bool   // alias names bound to different tables
	qualRefs [][2]string       // (qualifier, column) from qualified column refs
}

func (r *run) fail(format string, args ...any) {
	msg := fmt.Sprintf(format, args...)
	if r.seen[msg] || len(r.reasons) >= maxReasons {
		return
	}
	r.seen[msg] = true
	r.reasons = append(r.reasons, msg)
}

// Validate returns whether sql is a safe, single, read-only query and, if so, the SQL to run.
func (v Validator) Validate(sql string) Result {
	r := &run{p: v.P, seen: map[string]bool{}, ctes: map[string]bool{}, aliases: map[string]string{}, ambig: map[string]bool{}}
	fail := func() Result { return Result{Reasons: r.reasons} }

	if strings.TrimSpace(sql) == "" {
		r.fail("empty: no SQL given")
		return fail()
	}
	if len(sql) > maxSQLBytes {
		r.fail("too_long: SQL exceeds %d bytes", maxSQLBytes)
		return fail()
	}
	if strings.ContainsRune(sql, 0) {
		r.fail("invalid: NUL byte in SQL")
		return fail()
	}
	scan, err := pg_query.Scan(sql)
	if err != nil {
		r.fail("syntax_error: %s", oneLine(err.Error()))
		return fail()
	}
	for _, t := range scan.Tokens {
		if t.Token == pg_query.Token_SQL_COMMENT || t.Token == pg_query.Token_C_COMMENT {
			r.fail("comment: SQL comments are not allowed")
			return fail()
		}
	}
	tree, err := pg_query.Parse(sql)
	if err != nil {
		r.fail("syntax_error: %s", oneLine(err.Error()))
		return fail()
	}
	if len(tree.Stmts) != 1 {
		r.fail("multiple_statements: exactly one statement is allowed, got %d", len(tree.Stmts))
		return fail()
	}
	top := tree.Stmts[0].Stmt.GetSelectStmt()
	if top == nil {
		r.fail("not_select: only SELECT statements are allowed")
		return fail()
	}

	r.walk(tree.Stmts[0].Stmt.ProtoReflect())
	r.checkQualifiedColumns()
	if r.nodes > maxNodes {
		r.fail("too_complex: more than %d parse-tree nodes", maxNodes)
	}
	if r.subq > maxSubqueries {
		r.fail("too_complex: more than %d subqueries", maxSubqueries)
	}
	r.enforceLimit(top)
	if len(r.reasons) > 0 {
		return fail()
	}
	out, err := pg_query.Deparse(tree)
	if err != nil {
		r.fail("internal: could not deparse validated query: %s", oneLine(err.Error()))
		return fail()
	}
	return Result{OK: true, SQL: out}
}

func oneLine(s string) string { return strings.Join(strings.Fields(s), " ") }

// walk visits every protobuf message in the tree, applying the default-deny node list and node-specific rules.
func (r *run) walk(m protoreflect.Message) {
	if !m.IsValid() {
		return
	}
	name := string(m.Descriptor().Name())
	r.nodes++
	if r.nodes > maxNodes {
		return
	}
	if !allowedNodes[name] {
		r.fail("disallowed_construct: %s is not allowed", name)
		return
	}
	r.check(m.Interface())
	fields := m.Descriptor().Fields()
	for i := 0; i < fields.Len(); i++ {
		fd := fields.Get(i)
		if fd.Message() == nil || fd.IsMap() {
			continue
		}
		if fd.IsList() {
			l := m.Get(fd).List()
			for j := 0; j < l.Len(); j++ {
				r.walk(l.Get(j).Message())
			}
		} else if m.Has(fd) {
			r.walk(m.Get(fd).Message())
		}
	}
}

func (r *run) check(msg any) {
	switch n := msg.(type) {
	case *pg_query.SelectStmt:
		if n.IntoClause != nil {
			r.fail("select_into: SELECT INTO is not allowed")
		}
		if len(n.LockingClause) > 0 {
			r.fail("locking: FOR UPDATE/SHARE is not allowed")
		}
		if len(n.ValuesLists) > 0 {
			r.fail("values: VALUES lists are not allowed")
		}
		if n.WithClause != nil && n.WithClause.Recursive {
			r.fail("recursive: WITH RECURSIVE is not allowed")
		}
		if n.WithClause != nil {
			for _, c := range n.WithClause.Ctes {
				if cte := c.GetCommonTableExpr(); cte != nil {
					r.ctes[cte.Ctename] = true
				}
			}
		}
	case *pg_query.CommonTableExpr:
		r.subq++
		if n.Ctequery.GetSelectStmt() == nil {
			r.fail("cte_not_select: a WITH query must be a SELECT")
		}
	case *pg_query.SubLink:
		r.subq++
	case *pg_query.RangeSubselect:
		r.subq++
		if n.Lateral {
			r.fail("lateral: LATERAL is not allowed")
		}
	case *pg_query.RangeVar:
		r.rangeVar(n)
	case *pg_query.ColumnRef:
		r.columnRef(n)
	case *pg_query.FuncCall:
		r.funcCall(n)
	case *pg_query.TypeCast:
		r.typeName(n.TypeName)
	case *pg_query.A_Expr:
		r.aExpr(n)
	}
}

func (r *run) rangeVar(n *pg_query.RangeVar) {
	// The parser already folds unquoted identifiers to lower case; quoted ones keep their case and must match exactly.
	rel := n.Relname
	schema := n.Schemaname
	if n.Catalogname != "" || (schema != "" && schema != "public") {
		r.fail("disallowed_schema: %q is not allowed (only the public schema)", n.Schemaname)
		return
	}
	table := rel
	if schema == "" && r.ctes[rel] {
		table = "" // reference to a CTE defined in this statement
	} else if _, ok := r.p.Tables[rel]; !ok {
		r.fail("disallowed_table: %q is not allowed", n.Relname)
		return
	} else {
		r.tables = append(r.tables, rel)
	}
	bind := rel
	if n.Alias != nil {
		bind = n.Alias.Aliasname
	}
	if prev, ok := r.aliases[bind]; ok && prev != table {
		r.ambig[bind] = true
	}
	r.aliases[bind] = table
}

func (r *run) columnRef(n *pg_query.ColumnRef) {
	var parts []string
	for _, f := range n.Fields {
		if s := f.GetString_(); s != nil {
			parts = append(parts, s.Sval)
		} else if f.GetAStar() != nil {
			parts = append(parts, "*")
		}
	}
	switch len(parts) {
	case 1:
	case 2:
		if parts[1] != "*" {
			r.qualRefs = append(r.qualRefs, [2]string{parts[0], parts[1]})
		}
	default:
		r.fail("disallowed_column_ref: qualified names with more than two parts are not allowed")
	}
}

// checkQualifiedColumns verifies alias.column references that resolve unambiguously to an allow-listed table.
func (r *run) checkQualifiedColumns() {
	for _, q := range r.qualRefs {
		qual, col := q[0], q[1]
		if r.ambig[qual] {
			continue
		}
		table, ok := r.aliases[qual]
		if !ok {
			r.fail("unknown_qualifier: %q is not a table or alias in this query", qual)
			continue
		}
		if table == "" {
			continue // CTE columns are whatever the CTE selects
		}
		if !r.p.Tables[table][col] {
			r.fail("disallowed_column: %s.%s is not allowed", table, col)
		}
	}
}

func (r *run) funcCall(n *pg_query.FuncCall) {
	var names []string
	for _, f := range n.Funcname {
		if s := f.GetString_(); s != nil {
			names = append(names, s.Sval)
		}
	}
	if len(names) != 1 {
		r.fail("disallowed_function: schema-qualified function %q is not allowed", strings.Join(names, "."))
		return
	}
	if !r.p.Functions[names[0]] {
		r.fail("disallowed_function: %q is not allowed", names[0])
	}
	if n.FuncVariadic || n.AggWithinGroup {
		r.fail("disallowed_function: unsupported call form for %q", names[0])
	}
}

func (r *run) typeName(t *pg_query.TypeName) {
	if t == nil {
		return
	}
	var names []string
	for _, f := range t.Names {
		if s := f.GetString_(); s != nil {
			names = append(names, s.Sval)
		}
	}
	if len(names) == 0 || len(names) > 2 || (len(names) == 2 && names[0] != "pg_catalog") ||
		!allowedTypes[names[len(names)-1]] || len(t.ArrayBounds) > 0 {
		r.fail("disallowed_type: cast to %q is not allowed", strings.Join(names, "."))
	}
}

func (r *run) aExpr(n *pg_query.A_Expr) {
	if !allowedAExpr[n.Kind.String()] {
		r.fail("disallowed_operator: expression kind %s is not allowed", n.Kind)
		return
	}
	if n.Kind == pg_query.A_Expr_Kind_AEXPR_OP || n.Kind == pg_query.A_Expr_Kind_AEXPR_OP_ANY || n.Kind == pg_query.A_Expr_Kind_AEXPR_OP_ALL {
		if len(n.Name) != 1 || !allowedOps[n.Name[0].GetString_().GetSval()] {
			r.fail("disallowed_operator: operator is not allowed")
		}
	}
}

// enforceLimit makes sure the outermost query has a constant LIMIT no larger than the policy maximum.
func (r *run) enforceLimit(top *pg_query.SelectStmt) {
	if off := top.LimitOffset; off != nil && (off.GetAConst() == nil || off.GetAConst().GetIval() == nil) {
		r.fail("limit: OFFSET must be an integer constant")
	}
	// The deparser only prints LIMIT when LimitOption says "count"; set it whenever we write a limit.
	setLimit := func(n int) {
		top.LimitCount = pg_query.MakeAConstIntNode(int64(n), -1)
		if top.LimitOption != pg_query.LimitOption_LIMIT_OPTION_WITH_TIES {
			top.LimitOption = pg_query.LimitOption_LIMIT_OPTION_COUNT
		}
	}
	if top.LimitCount == nil {
		setLimit(r.p.DefaultLimit)
		return
	}
	c := top.LimitCount.GetAConst()
	switch {
	case c == nil:
		r.fail("limit: LIMIT must be a constant number")
	case c.Isnull: // LIMIT ALL / LIMIT NULL
		setLimit(r.p.MaxLimit)
	case c.GetIval() == nil:
		r.fail("limit: LIMIT must be an integer")
	case int(c.GetIval().Ival) > r.p.MaxLimit:
		setLimit(r.p.MaxLimit)
	case c.GetIval().Ival < 0:
		r.fail("limit: LIMIT must not be negative")
	}
}
