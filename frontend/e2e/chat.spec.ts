import { expect, test, type Page } from "@playwright/test";

/** The API is mocked at the network layer, so these tests need no backend, DB or LLM. */
function sse(events: [string, unknown][]): string {
  return events.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join("");
}

const base = {
  trace_id: "t1", reason: null, flags: [], ungrounded: [], latency_s: 2.1, tokens: 900, shadow_cost_usd: 0.001,
};

async function mockChat(page: Page, body: string, status = 200) {
  await page.route("**/api/chat", (route) =>
    route.fulfill({ status, contentType: status === 200 ? "text/event-stream" : "application/json", body }),
  );
}

test("answers with steps, then opens a source", async ({ page }) => {
  await mockChat(
    page,
    sse([
      ["start", { question: "q" }],
      ["step", { type: "tool", name: "financial_sql", args: { question: "Apple revenue FY2024" }, ok: true, summary: "1 rows" }],
      ["answer", {
        ...base, status: "answered",
        answer: "Apple's FY2024 revenue was **$391.04 billion** [sql1].",
        citations: [{ type: "sql", id: "sql1", label: "Reported XBRL financial data", sql: "SELECT value FROM xbrl_facts LIMIT 50", columns: ["value"], rows: [[391035000000]], truncated: false }],
      }],
    ]),
  );
  await page.goto("/");
  await page.getByLabel("Your question").fill("What was Apple's revenue in fiscal 2024?");
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByText("Querying reported financials")).toBeVisible();
  await expect(page.getByText("$391.04 billion")).toBeVisible();
  await page.getByRole("button", { name: "Source sql1" }).click();
  const panel = page.getByRole("complementary", { name: "Sources" });
  await expect(panel).toContainText("SELECT value FROM xbrl_facts");
  await expect(panel).toContainText("391,035,000,000");
  await page.getByRole("button", { name: "Close sources" }).click();
  await expect(panel).toBeHidden();
});

test("shows a refusal badge", async ({ page }) => {
  await mockChat(page, sse([["answer", { ...base, status: "refused", reason: "model_declined", answer: "Tesla is not covered.", citations: [] }]]));
  await page.goto("/");
  await page.getByRole("button", { name: /Should I buy Tesla stock/ }).click();
  await expect(page.getByText("Not enough evidence in the filings")).toBeVisible();
});

test("shows a friendly message when the server is busy", async ({ page }) => {
  await mockChat(page, JSON.stringify({ detail: "busy" }), 429);
  await page.goto("/");
  await page.getByLabel("Your question").fill("What are Meta's main risks?");
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.locator(".notice.error[role=alert]")).toContainText("busy");
});

test("a dropped stream is reported, not silent", async ({ page }) => {
  await mockChat(page, sse([["step", { type: "tool", name: "calculator", args: { expression: "1+1" }, ok: true, summary: "= 2" }]]));
  await page.goto("/");
  await page.getByLabel("Your question").fill("What is one plus one?");
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.locator(".notice.error[role=alert]")).toContainText("before an answer");
});
