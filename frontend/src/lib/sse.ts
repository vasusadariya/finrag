/** Incremental parser for server-sent events (`event:` + `data:` blocks separated by blank lines). */

export interface RawEvent {
  event: string;
  data: string;
}

export class SseParser {
  private buffer = "";

  /** Feed a decoded text chunk; returns every event completed by it. */
  push(chunk: string): RawEvent[] {
    this.buffer += chunk.replace(/\r\n/g, "\n");
    const out: RawEvent[] = [];
    let idx: number;
    while ((idx = this.buffer.indexOf("\n\n")) !== -1) {
      const block = this.buffer.slice(0, idx);
      this.buffer = this.buffer.slice(idx + 2);
      const parsed = parseBlock(block);
      if (parsed) out.push(parsed);
    }
    return out;
  }
}

function parseBlock(block: string): RawEvent | null {
  let event = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith(":")) continue; // comment / keep-alive
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }
  return data.length ? { event, data: data.join("\n") } : null;
}
