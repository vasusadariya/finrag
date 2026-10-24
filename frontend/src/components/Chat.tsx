"use client";

import { useEffect, useRef, useState } from "react";
import { useChat } from "@/hooks/useChat";
import { Composer } from "./Composer";
import { Message } from "./Message";
import { SourcesPanel } from "./SourcesPanel";

export const EXAMPLES = [
  "How did Apple's R&D spending change from fiscal 2024 to 2025?",
  "What are NVIDIA's main supply chain risks?",
  "What was Microsoft's operating margin in fiscal 2026?",
  "Should I buy Tesla stock?",
];

export function Chat() {
  const { turns, busy, send, stop, clear } = useChat();
  const [selected, setSelected] = useState<{ turnId: number; id: string } | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [turns]);

  const open = selected ? turns.find((t) => t.id === selected.turnId)?.answer?.citations : undefined;

  return (
    <div className={open ? "layout with-panel" : "layout"}>
      <div className="shell">
      <header className="top">
        <h1>FinRAG</h1>
        <span className="tag">Cited answers from SEC 10-K filings · AAPL MSFT GOOGL AMZN NVDA META JPM XOM</span>
        {turns.length > 0 && !busy && (
          <button className="link" onClick={clear}>
            New chat
          </button>
        )}
      </header>
      <main className="thread" aria-live="polite">
        {turns.length === 0 && (
          <section className="empty">
            <p>Ask a question about the annual reports. Every answer cites its sources, and the assistant says so when the filings don&apos;t cover something.</p>
            <ul className="examples">
              {EXAMPLES.map((q) => (
                <li key={q}>
                  <button onClick={() => send(q)}>{q}</button>
                </li>
              ))}
            </ul>
          </section>
        )}
        {turns.map((t) => (
          <Message key={t.id} turn={t} onCite={(turnId, id) => setSelected({ turnId, id })} />
        ))}
        <div ref={bottom} />
      </main>
      <Composer busy={busy} onSend={send} onStop={stop} />
      </div>
      {open && selected && (
        <SourcesPanel
          citations={open}
          selectedId={selected.id}
          onSelect={(id) => setSelected({ turnId: selected.turnId, id })}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}
