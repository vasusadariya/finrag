/** Turning answer text into renderable parts. Everything is plain data; React escapes the rest. */

export type Part = { kind: "text"; text: string } | { kind: "cite"; id: string };

const CITE_GROUP = /\[\s*((?:c|sql)\d+(?:\s*,\s*(?:c|sql)\d+)*)\s*\]/gi;

/** Split text into text and citation parts: "x [c1, sql2]" -> text, cite c1, cite sql2. */
export function splitCitations(text: string): Part[] {
  const parts: Part[] = [];
  let last = 0;
  for (const m of text.matchAll(CITE_GROUP)) {
    const start = m.index ?? 0;
    if (start > last) parts.push({ kind: "text", text: text.slice(last, start) });
    for (const id of (m[1] ?? "").split(",")) parts.push({ kind: "cite", id: id.trim().toLowerCase() });
    last = start + m[0].length;
  }
  if (last < text.length) parts.push({ kind: "text", text: text.slice(last) });
  return parts;
}

export type Block =
  | { kind: "p"; text: string }
  | { kind: "ul"; items: string[] };

/** Paragraphs and bullet lists (`* ` / `- `); anything else is a paragraph line. */
export function toBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    if (!line.trim()) continue;
    const bullet = /^\s*[*-]\s+(.*)$/.exec(line);
    if (bullet) {
      const last = blocks[blocks.length - 1];
      if (last?.kind === "ul") last.items.push(bullet[1] ?? "");
      else blocks.push({ kind: "ul", items: [bullet[1] ?? ""] });
    } else {
      blocks.push({ kind: "p", text: line.trim() });
    }
  }
  return blocks;
}

/** Split `**bold**` spans out of a text fragment. */
export function splitBold(text: string): { bold: boolean; text: string }[] {
  const out: { bold: boolean; text: string }[] = [];
  text.split(/(\*\*[^*]+\*\*)/g).forEach((seg) => {
    if (!seg) return;
    if (seg.startsWith("**") && seg.endsWith("**") && seg.length > 4)
      out.push({ bold: true, text: seg.slice(2, -2) });
    else out.push({ bold: false, text: seg });
  });
  return out;
}

export const TOOL_LABELS: Record<string, string> = {
  filing_search: "Searching filings",
  financial_sql: "Querying reported financials",
  calculator: "Calculating",
};

export function describeToolArgs(name: string, args: Record<string, unknown>): string {
  const pick = (k: string) => (typeof args[k] === "string" ? (args[k] as string) : "");
  if (name === "filing_search") {
    const where = [pick("ticker"), args.fiscal_year ? `FY${String(args.fiscal_year)}` : "", pick("section")]
      .filter(Boolean)
      .join(" ");
    return [pick("query"), where && `(${where})`].filter(Boolean).join(" ");
  }
  if (name === "financial_sql") return pick("question");
  if (name === "calculator") return pick("expression");
  return "";
}
