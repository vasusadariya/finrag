/** Wire types for the FinRAG API (mirror backend/app/api/main.py and backend/app/agent). */

export type Status = "answered" | "refused" | "error";

export interface FilingCitation {
  type: "filing";
  id: string;
  chunk_id: number;
  ticker: string;
  company: string;
  fiscal_year: number;
  section: string;
  page: number | null;
  accession: string;
  label: string;
}

export interface SqlCitation {
  type: "sql";
  id: string;
  label: string;
  sql: string;
  columns: string[];
  rows: unknown[][];
  truncated: boolean;
}

export type Citation = FilingCitation | SqlCitation;

export interface ToolEvent {
  type: "tool";
  name: string;
  args: Record<string, unknown>;
  ok: boolean;
  summary: string;
}

export interface Answer {
  trace_id: string | null;
  status: Status;
  answer: string;
  reason: string | null;
  citations: Citation[];
  flags: string[];
  ungrounded: string[];
  latency_s: number;
  tokens: number;
  shadow_cost_usd: number;
}

export type ChatEvent =
  | { event: "start"; data: { question: string } }
  | { event: "step"; data: ToolEvent }
  | { event: "answer"; data: Answer }
  | { event: "error"; data: { error: string } };

export interface Health {
  status: "ok" | "degraded";
  db: boolean;
  sql_guard: boolean;
}
