import { SseParser } from "./sse";
import type { ChatEvent, Health } from "./types";

/** `/api` is proxied to FastAPI by Next (no CORS); set NEXT_PUBLIC_API_URL to bypass the proxy. */
export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "/api";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const KNOWN = new Set(["start", "step", "answer", "error"]);

export function toChatEvent(event: string, data: string): ChatEvent | null {
  if (!KNOWN.has(event)) return null;
  try {
    return { event, data: JSON.parse(data) } as ChatEvent;
  } catch {
    return null;
  }
}

async function failure(res: Response): Promise<ApiError> {
  let detail = res.statusText;
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === "string") detail = body.detail;
    else if (body.detail) detail = "invalid request";
  } catch {
    /* keep statusText */
  }
  const message =
    res.status === 429 ? "The analyst is busy right now. Try again in a few seconds." : detail;
  return new ApiError(message, res.status);
}

/** Stream one question; `onEvent` fires for each server event. Resolves when the stream ends. */
export async function streamChat(
  question: string,
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal,
  fetchImpl: typeof fetch = fetch,
): Promise<void> {
  const res = await fetchImpl(`${API_BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ question, stream: true }),
    signal,
  });
  if (!res.ok) throw await failure(res);
  if (!res.body) throw new ApiError("The server sent no response body.", res.status);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    for (const raw of parser.push(decoder.decode(value, { stream: true }))) {
      const evt = toChatEvent(raw.event, raw.data);
      if (evt) onEvent(evt);
    }
  }
}

export async function getHealth(fetchImpl: typeof fetch = fetch): Promise<Health> {
  const res = await fetchImpl(`${API_BASE}/health`);
  // /health answers 503 with a body when degraded; only other failures are errors
  if (!res.ok && res.status !== 503) throw await failure(res);
  return (await res.json()) as Health;
}
