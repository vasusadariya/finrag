import type { Answer, ChatEvent, ToolEvent } from "@/lib/types";

export interface Turn {
  id: number;
  question: string;
  steps: ToolEvent[];
  answer?: Answer;
  error?: string;
  pending: boolean;
}

export type Action =
  | { type: "ask"; id: number; question: string }
  | { type: "event"; id: number; event: ChatEvent }
  | { type: "fail"; id: number; message: string }
  | { type: "end"; id: number }
  | { type: "clear" };

export function chatReducer(state: Turn[], action: Action): Turn[] {
  switch (action.type) {
    case "ask":
      return [...state, { id: action.id, question: action.question, steps: [], pending: true }];
    case "clear":
      return [];
    default:
      return state.map((t) => (t.id === action.id ? turn(t, action) : t));
  }
}

function turn(t: Turn, action: Action): Turn {
  if (action.type === "fail") return { ...t, error: action.message, pending: false };
  if (action.type === "end") {
    // a stream that ended with neither an answer nor an error is a failure the user must see
    const incomplete = !t.answer && !t.error;
    return { ...t, pending: false, error: incomplete ? "The connection ended before an answer arrived." : t.error };
  }
  if (action.type !== "event") return t;
  const e = action.event;
  if (e.event === "step") return { ...t, steps: [...t.steps, e.data] };
  if (e.event === "answer") return { ...t, answer: e.data, pending: false };
  if (e.event === "error") return { ...t, error: e.data.error, pending: false };
  return t;
}
