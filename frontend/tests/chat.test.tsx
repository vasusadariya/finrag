import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Chat } from "@/components/Chat";
import * as api from "@/lib/api";
import type { Answer, ChatEvent } from "@/lib/types";

vi.mock("@/lib/api");

const answer = (over: Partial<Answer> = {}): Answer => ({
  trace_id: "t1", status: "answered", answer: "Revenue was **$391.04 billion** [sql1].", reason: null,
  citations: [{ type: "sql", id: "sql1", label: "XBRL", sql: "SELECT 1", columns: ["v"], rows: [[1]], truncated: false }],
  flags: [], ungrounded: [], latency_s: 2.3, tokens: 100, shadow_cost_usd: 0, ...over,
});

function script(events: ChatEvent[]) {
  vi.mocked(api.streamChat).mockImplementation(async (_q, onEvent) => {
    for (const e of events) onEvent(e);
  });
}

beforeEach(() => vi.resetAllMocks());

describe("Chat", () => {
  it("shows steps and a cited answer for a question", async () => {
    script([
      { event: "step", data: { type: "tool", name: "financial_sql", args: { question: "Apple revenue" }, ok: true, summary: "1 rows" } },
      { event: "answer", data: answer() },
    ]);
    const user = userEvent.setup();
    render(<Chat />);
    await user.type(screen.getByLabelText("Your question"), "Apple revenue 2024?");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(screen.getByText("$391.04 billion")).toBeInTheDocument());
    expect(screen.getByText("Querying reported financials")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Source sql1" })).toBeInTheDocument();
    expect(api.streamChat).toHaveBeenCalledWith("Apple revenue 2024?", expect.any(Function), expect.any(AbortSignal));
  });

  it("marks refusals and unverified figures", async () => {
    script([{ event: "answer", data: answer({ status: "refused", reason: "model_declined", answer: "Tesla is not covered.", citations: [] }) }]);
    const user = userEvent.setup();
    render(<Chat />);
    await user.click(screen.getByRole("button", { name: /Should I buy Tesla stock/ }));
    expect(await screen.findByText("Not enough evidence in the filings")).toBeInTheDocument();
    expect(screen.getByText("Tesla is not covered.")).toBeInTheDocument();
  });

  it("shows an error when the request fails and lets the user retry", async () => {
    vi.mocked(api.streamChat).mockRejectedValueOnce(new Error("The analyst is busy right now."));
    const user = userEvent.setup();
    render(<Chat />);
    await user.type(screen.getByLabelText("Your question"), "What is Apple's HQ?");
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("busy");
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled(); // empty box
  });

  it("does not send too-short or over-long questions", async () => {
    const user = userEvent.setup();
    render(<Chat />);
    const box = screen.getByLabelText("Your question");
    await user.type(box, "hi");
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
    await user.clear(box);
    await user.click(box);
    await user.paste("x".repeat(1001));
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
    expect(screen.getByText(/too long/)).toBeInTheDocument();
    expect(api.streamChat).not.toHaveBeenCalled();
  });
});

describe("Sources panel", () => {
  it("opens the SQL source with its rows, and a filing source with its passage", async () => {
    const a = answer({
      answer: "Risk exists [c1]. Revenue was $391.0 billion [sql1].",
      citations: [
        { type: "filing", id: "c1", chunk_id: 1, ticker: "AAPL", company: "Apple Inc.", fiscal_year: 2024,
          section: "Item 1A", page: 9, accession: "0000320193-24-000123", label: "AAPL FY2024 10-K, Item 1A, p.9",
          text: "Supply chain disruptions could harm us." },
        { type: "sql", id: "sql1", label: "Reported XBRL financial data", sql: "SELECT value FROM xbrl_facts LIMIT 50",
          columns: ["value"], rows: [[391035000000]], truncated: false },
      ],
    });
    script([{ event: "answer", data: a }]);
    const user = userEvent.setup();
    render(<Chat />);
    await user.type(screen.getByLabelText("Your question"), "Apple risks and revenue?");
    await user.keyboard("{Enter}");
    await user.click(await screen.findByRole("button", { name: "Source c1" }));
    const panel = screen.getByRole("complementary", { name: "Sources" });
    expect(panel).toHaveTextContent("Supply chain disruptions could harm us.");
    expect(panel).toHaveTextContent("Item 1A");
    await user.click(screen.getByRole("button", { name: "Source sql1" }));
    expect(panel).toHaveTextContent("SELECT value FROM xbrl_facts LIMIT 50");
    expect(panel).toHaveTextContent("391,035,000,000");
    await user.click(screen.getByRole("button", { name: "Close sources" }));
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
  });
});
