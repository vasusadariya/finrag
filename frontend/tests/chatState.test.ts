import { describe, expect, it } from "vitest";
import { chatReducer, type Turn } from "@/hooks/chatState";
import type { Answer } from "@/lib/types";

const answer: Answer = {
  trace_id: null, status: "answered", answer: "x", reason: null, citations: [], flags: [],
  ungrounded: [], latency_s: 1, tokens: 1, shadow_cost_usd: 0,
};

function run(actions: Parameters<typeof chatReducer>[1][]): Turn[] {
  return actions.reduce(chatReducer, [] as Turn[]);
}

describe("chatReducer", () => {
  it("collects steps then the answer", () => {
    const s = run([
      { type: "ask", id: 1, question: "q" },
      { type: "event", id: 1, event: { event: "step", data: { type: "tool", name: "calculator", args: {}, ok: true, summary: "= 1" } } },
      { type: "event", id: 1, event: { event: "answer", data: answer } },
      { type: "end", id: 1 },
    ]);
    expect(s[0]).toMatchObject({ pending: false, steps: [{ name: "calculator" }], answer: { status: "answered" } });
    expect(s[0]?.error).toBeUndefined();
  });

  it("turns a stream that ends silently into a visible error", () => {
    const s = run([{ type: "ask", id: 1, question: "q" }, { type: "end", id: 1 }]);
    expect(s[0]?.error).toMatch(/before an answer/);
  });

  it("keeps other turns untouched and supports clear", () => {
    const s = run([
      { type: "ask", id: 1, question: "a" },
      { type: "ask", id: 2, question: "b" },
      { type: "fail", id: 2, message: "boom" },
    ]);
    expect(s[0]?.pending).toBe(true);
    expect(s[1]).toMatchObject({ error: "boom", pending: false });
    expect(chatReducer(s, { type: "clear" })).toEqual([]);
  });

  it("surfaces server error events", () => {
    const s = run([
      { type: "ask", id: 1, question: "q" },
      { type: "event", id: 1, event: { event: "error", data: { error: "internal error" } } },
    ]);
    expect(s[0]).toMatchObject({ error: "internal error", pending: false });
  });
});
