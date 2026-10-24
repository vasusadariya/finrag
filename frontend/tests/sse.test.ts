import { describe, expect, it } from "vitest";
import { SseParser } from "@/lib/sse";

describe("SseParser", () => {
  it("parses complete events", () => {
    const p = new SseParser();
    expect(p.push('event: step\ndata: {"a":1}\n\nevent: answer\ndata: {"b":2}\n\n')).toEqual([
      { event: "step", data: '{"a":1}' },
      { event: "answer", data: '{"b":2}' },
    ]);
  });

  it("handles events split across chunks, even mid-line", () => {
    const p = new SseParser();
    expect(p.push("event: ste")).toEqual([]);
    expect(p.push('p\ndata: {"x"')).toEqual([]);
    expect(p.push(":1}\n")).toEqual([]);
    expect(p.push("\n")).toEqual([{ event: "step", data: '{"x":1}' }]);
  });

  it("supports CRLF, comments, default event and multi-line data", () => {
    const p = new SseParser();
    const out = p.push(": keep-alive\r\n\r\ndata: one\r\ndata: two\r\n\r\n");
    expect(out).toEqual([{ event: "message", data: "one\ntwo" }]);
  });

  it("drops blocks without data", () => {
    expect(new SseParser().push("event: ping\n\n")).toEqual([]);
  });
});
