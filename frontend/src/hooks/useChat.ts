"use client";

import { useCallback, useReducer, useRef } from "react";
import { streamChat } from "@/lib/api";
import { chatReducer } from "./chatState";

export const MAX_QUESTION = 1000;

export function useChat() {
  const [turns, dispatch] = useReducer(chatReducer, []);
  const nextId = useRef(1);
  const abort = useRef<AbortController | null>(null);

  const busy = turns.some((t) => t.pending);

  const send = useCallback(
    async (raw: string) => {
      const question = raw.trim();
      if (question.length < 3 || question.length > MAX_QUESTION || busy) return;
      const id = nextId.current++;
      const controller = new AbortController();
      abort.current = controller;
      dispatch({ type: "ask", id, question });
      try {
        await streamChat(question, (event) => dispatch({ type: "event", id, event }), controller.signal);
        dispatch({ type: "end", id });
      } catch (e) {
        if (controller.signal.aborted) dispatch({ type: "fail", id, message: "Stopped." });
        else dispatch({ type: "fail", id, message: e instanceof Error ? e.message : "Request failed." });
      }
    },
    [busy],
  );

  const stop = useCallback(() => abort.current?.abort(), []);
  const clear = useCallback(() => dispatch({ type: "clear" }), []);
  return { turns, busy, send, stop, clear };
}
