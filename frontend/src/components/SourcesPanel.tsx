import type { Citation } from "@/lib/types";

function formatCell(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString("en-US") : String(v);
  return String(v);
}

function Detail({ c }: { c: Citation }) {
  if (c.type === "filing") {
    return (
      <>
        <h3>{c.label}</h3>
        <dl className="facts">
          <dt>Company</dt><dd>{c.company} ({c.ticker})</dd>
          <dt>Fiscal year</dt><dd>{c.fiscal_year}</dd>
          <dt>Section</dt><dd>{c.section}</dd>
          <dt>Page</dt><dd>{c.page ?? "n/a"}</dd>
          <dt>Filing</dt><dd>{c.accession}</dd>
        </dl>
        <blockquote className="passage">{c.text}</blockquote>
      </>
    );
  }
  return (
    <>
      <h3>{c.label}</h3>
      <p className="muted">Query that produced these figures (checked by the SQL guard, read-only):</p>
      <pre className="sql">{c.sql}</pre>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>{c.columns.map((col) => <th key={col}>{col}</th>)}</tr>
          </thead>
          <tbody>
            {c.rows.map((row, i) => (
              <tr key={i}>{row.map((cell, j) => <td key={j}>{formatCell(cell)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
      {c.truncated && <p className="muted">Showing the first rows only.</p>}
    </>
  );
}

interface Props {
  citations: Citation[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onClose: () => void;
}

export function SourcesPanel({ citations, selectedId, onSelect, onClose }: Props) {
  const selected = citations.find((c) => c.id === selectedId) ?? citations[0];
  if (!selected) return null;
  return (
    <aside className="sources" aria-label="Sources">
      <div className="sources-head">
        <strong>Sources</strong>
        <button className="link" onClick={onClose} aria-label="Close sources">
          Close
        </button>
      </div>
      <div className="tabs" role="tablist">
        {citations.map((c) => (
          <button
            key={c.id}
            role="tab"
            aria-selected={c.id === selected.id}
            className={c.id === selected.id ? "tab on" : "tab"}
            onClick={() => onSelect(c.id)}
          >
            {c.id}
          </button>
        ))}
      </div>
      <Detail c={selected} />
    </aside>
  );
}
