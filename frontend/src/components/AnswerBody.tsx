import { splitBold, splitCitations, toBlocks } from "@/lib/format";

interface Props {
  text: string;
  onCite: (id: string) => void;
  knownIds: Set<string>;
}

function Inline({ text, onCite, knownIds }: Props) {
  return (
    <>
      {splitCitations(text).map((part, i) =>
        part.kind === "cite" ? (
          knownIds.has(part.id) ? (
            <button key={i} className="cite" onClick={() => onCite(part.id)} aria-label={`Source ${part.id}`}>
              {part.id}
            </button>
          ) : null
        ) : (
          splitBold(part.text).map((s, j) =>
            s.bold ? <strong key={`${i}-${j}`}>{s.text}</strong> : <span key={`${i}-${j}`}>{s.text}</span>,
          )
        ),
      )}
    </>
  );
}

export function AnswerBody({ text, onCite, knownIds }: Props) {
  return (
    <div className="answer-body">
      {toBlocks(text).map((b, i) =>
        b.kind === "ul" ? (
          <ul key={i}>
            {b.items.map((it, j) => (
              <li key={j}>
                <Inline text={it} onCite={onCite} knownIds={knownIds} />
              </li>
            ))}
          </ul>
        ) : (
          <p key={i}>
            <Inline text={b.text} onCite={onCite} knownIds={knownIds} />
          </p>
        ),
      )}
    </div>
  );
}
