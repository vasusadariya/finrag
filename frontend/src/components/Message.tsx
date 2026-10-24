import type { Turn } from "@/hooks/chatState";
import { AnswerBody } from "./AnswerBody";
import { StepTimeline } from "./StepTimeline";

const REFUSAL_HINT: Record<string, string> = {
  model_declined: "Not enough evidence in the filings",
  no_evidence: "No supporting evidence found",
  uncited: "Could not tie the answer to a source",
};

export function Message({ turn, onCite }: { turn: Turn; onCite: (turnId: number, id: string) => void }) {
  const { answer } = turn;
  const known = new Set((answer?.citations ?? []).map((c) => c.id));
  return (
    <article className="turn">
      <div className="question">{turn.question}</div>
      <StepTimeline steps={turn.steps} pending={turn.pending} />
      {turn.error && (
        <div className="notice error" role="alert">
          {turn.error}
        </div>
      )}
      {answer && (
        <div className={`answer ${answer.status}`}>
          {answer.status === "refused" && (
            <span className="badge refused">{REFUSAL_HINT[answer.reason ?? ""] ?? "Declined to answer"}</span>
          )}
          {answer.flags.includes("ungrounded_numbers") && (
            <span className="badge warn" title={answer.ungrounded.join(", ")}>
              Some figures could not be verified
            </span>
          )}
          <AnswerBody text={answer.answer} knownIds={known} onCite={(id) => onCite(turn.id, id)} />
          <div className="meta">
            {answer.latency_s.toFixed(1)}s · {answer.citations.length} source
            {answer.citations.length === 1 ? "" : "s"}
          </div>
        </div>
      )}
    </article>
  );
}
