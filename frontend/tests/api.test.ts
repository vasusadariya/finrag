import { describe, expect, it, vi } from "vitest";
import { ApiError, getHealth, streamChat, toChatEvent } from "@/lib/api";
import type { ChatEvent } from "@/lib/types";

function sseResponse(chunks: string[], init: ResponseInit = { status: 200 }): Response {
  const enc = new TextEncoder();
  const stream = new ReadableStream({
    start(c) {
      for (const ch of chunks) c.enqueue(enc.encode(ch));
      c.close();
    },
  });
  return new Response(stream, init);
}

describe("streamChat", () => {
  it("delivers typed events in order and ignores unknown ones", async () => {
    const body = [
      'event: start\ndata: {"question":"q"}\n\n',
      'event: step\ndata: {"type":"tool","name":"calculator","args":{},"ok":true,"summary":"= 2"}\n\nevent: wat\ndata: {}\n\n',
      'event: answer\ndata: {"status":"answered","answer":"hi","citations":[]}\n\n',
    ];
    const f = vi.fn().mockResolvedValue(sseResponse(body));
    const seen: ChatEvent[] = [];
    await streamChat("q", (e) => seen.push(e), undefined, f);
    expect(seen.map((e) => e.event)).toEqual(["start", "step", "answer"]);
    const [url, init] = f.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/chat");
    expect(JSON.parse(init.body as string)).toEqual({ question: "q", stream: true });
  });

  it("maps HTTP errors to ApiError with friendly text for 429", async () => {
    const busy = vi.fn().mockResolvedValue(new Response("{}", { status: 429 }));
    await expect(streamChat("q", () => {}, undefined, busy)).rejects.toMatchObject({
      status: 429,
      message: expect.stringContaining("busy"),
    });
    const bad = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "nope" }), { status: 400 }),
    );
    await expect(streamChat("q", () => {}, undefined, bad)).rejects.toBeInstanceOf(ApiError);
  });
});

describe("toChatEvent", () => {
  it("rejects malformed JSON and unknown events", () => {
    expect(toChatEvent("answer", "{oops")).toBeNull();
    expect(toChatEvent("nope", "{}")).toBeNull();
    expect(toChatEvent("error", '{"error":"x"}')).toEqual({ event: "error", data: { error: "x" } });
  });
});

describe("getHealth", () => {
  it("returns the body of a degraded 503", async () => {
    const f = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "degraded", db: false, sql_guard: true }), {
        status: 503,
      }),
    );
    expect((await getHealth(f)).status).toBe("degraded");
  });
});
