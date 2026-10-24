import { TOOL_LABELS, describeToolArgs } from "@/lib/format";
import type { ToolEvent } from "@/lib/types";

export function StepTimeline({ steps, pending }: { steps: ToolEvent[]; pending: boolean }) {
  if (steps.length === 0 && !pending) return null;
  return (
    <ol className="timeline" aria-label="Agent steps">
      {steps.map((s, i) => (
        <li key={i} className={s.ok ? "step ok" : "step fail"}>
          <span className="step-name">{TOOL_LABELS[s.name] ?? s.name}</span>
          <span className="step-args">{describeToolArgs(s.name, s.args)}</span>
          <span className="step-result">{s.ok ? s.summary : `failed: ${s.summary}`}</span>
        </li>
      ))}
      {pending && (
        <li className="step running" role="status">
          <span className="step-name">Thinking…</span>
        </li>
      )}
    </ol>
  );
}
