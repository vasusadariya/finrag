"use client";

import { useState } from "react";
import { MAX_QUESTION } from "@/hooks/useChat";

interface Props {
  busy: boolean;
  onSend: (q: string) => void;
  onStop: () => void;
}

export function Composer({ busy, onSend, onStop }: Props) {
  const [value, setValue] = useState("");
  const tooLong = value.length > MAX_QUESTION;
  const canSend = !busy && value.trim().length >= 3 && !tooLong;

  const submit = () => {
    if (!canSend) return;
    onSend(value);
    setValue("");
  };

  return (
    <form
      className="composer"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <textarea
        aria-label="Your question"
        placeholder="Ask about a company's 10-K, e.g. How did Apple's R&D spending change from 2024 to 2025?"
        value={value}
        rows={2}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
      />
      {busy ? (
        <button type="button" onClick={onStop}>
          Stop
        </button>
      ) : (
        <button type="submit" disabled={!canSend}>
          Ask
        </button>
      )}
      {tooLong && <span className="notice error">Question is too long ({value.length}/{MAX_QUESTION}).</span>}
    </form>
  );
}
